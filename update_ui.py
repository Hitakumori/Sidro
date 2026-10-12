"""Nonblocking optional update UI shared by startup and Settings."""
from datetime import date
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

import config
from update_core import automatic_check_due, check_release, download_release


class UpdateController:
    def __init__(self, ui, is_busy):
        self.ui = ui
        self.is_busy = is_busy
        self.busy = False
        self.progress = None
        self.cancelled = threading.Event()
        self.check_date_path = config.USER_DATA_DIR / 'update_check.json'
        ui.app.sidro_updates = self
        session = os.environ.pop('SIDRO_UPDATE_SESSION', '')
        if re.fullmatch(r'update-[a-zA-Z0-9_-]{1,64}', session):
            ready = config.USER_DATA_DIR / 'updates' / session / 'ready'
            def signal_ready():
                try:
                    if ready.parent.is_dir():
                        ready.write_text(config.APP_VERSION, encoding='utf-8')
                except OSError:
                    pass
            QTimer.singleShot(1000, signal_ready)
        QTimer.singleShot(6000, self.check_automatically)

    def parent(self):
        return QApplication.activeModalWidget() or self.ui.dashboard

    def tasks_active(self):
        return self.is_busy()

    def check_automatically(self):
        last_checked = None
        try:
            cached = json.loads(self.check_date_path.read_text(encoding='utf-8'))
            last_checked = cached.get('date')
        except (OSError, ValueError, AttributeError):
            pass
        if automatic_check_due(config.UPDATE_CHECK_FREQUENCY, last_checked):
            self.check(manual=False)

    def check(self, manual=True):
        if self.busy:
            if manual:
                QMessageBox.information(self.parent(), 'Sidro updates', 'An update check or download is already running.')
            return
        self.busy = True

        def worker():
            try:
                release = check_release(config.APP_VERSION)
                try:
                    self.check_date_path.write_text(json.dumps({'date': date.today().isoformat()}), encoding='utf-8')
                except OSError:
                    pass
                self.ui.invoke(lambda: self.checked(release, manual))
            except Exception as exc:
                self.ui.invoke(lambda error=str(exc): self.check_failed(error, manual))
        threading.Thread(target=worker, name='Sidro: Update check', daemon=True).start()

    def check_failed(self, error, manual):
        self.busy = False
        if manual:
            QMessageBox.warning(self.parent(), 'Sidro updates', f'Could not check for updates. Check your connection and try again.\n\n{error}')

    def checked(self, release, manual):
        self.busy = False
        if release is None:
            if manual:
                QMessageBox.information(self.parent(), 'Sidro updates', f'Sidro {config.APP_VERSION}: no newer stable release is available.')
            return
        dialog = QMessageBox(self.parent())
        dialog.setWindowTitle('Sidro update available')
        dialog.setText(f"Sidro {release['version']} is available.\nInstalled: {config.APP_VERSION}")
        available = config.IS_FROZEN and release['asset'] is not None and (config.APP_DIR / 'SidroUpdater.exe').is_file()
        install = dialog.addButton('Update now', QMessageBox.AcceptRole) if available else None
        if available:
            dialog.setInformativeText('Download the update, then restart Sidro to install it. Your settings and saved sign-ins are preserved.')
        else:
            dialog.setInformativeText('Open the release page for downloads. Automatic installation requires a packaged Windows build and a verified release ZIP.')
        notes = dialog.addButton('Release notes', QMessageBox.ActionRole)
        later = dialog.addButton('Later', QMessageBox.RejectRole)
        dialog.setDefaultButton(later)
        dialog.exec()
        if dialog.clickedButton() == notes:
            QDesktopServices.openUrl(QUrl(release['page']))
        elif install is not None and dialog.clickedButton() == install:
            self.download(release)

    def download(self, release):
        if self.tasks_active():
            QMessageBox.information(self.parent(), 'Sidro updates', 'Finish active tasks and turn off live scanning before updating Sidro.')
            return
        self.busy = True
        self.cancelled.clear()
        self.progress = QProgressDialog('Downloading and verifying the Sidro update…', 'Cancel', 0, 100, self.parent())
        self.progress.setWindowTitle('Sidro update')
        self.progress.setWindowModality(Qt.ApplicationModal)
        self.progress.setAutoClose(False)
        self.progress.setAutoReset(False)
        self.progress.canceled.connect(self.cancelled.set)
        self.progress.show()

        def worker():
            work = None
            try:
                updates = config.USER_DATA_DIR / 'updates'
                updates.mkdir(parents=True, exist_ok=True)
                work = Path(tempfile.mkdtemp(prefix='update-', dir=updates))
                download_release(release['asset'], work / 'release.zip',
                    progress=lambda percent: self.ui.invoke(lambda value=percent: self.progress.setValue(value)),
                    cancelled=self.cancelled.is_set)
                if self.cancelled.is_set():
                    raise InterruptedError('Update download cancelled.')
                self.ui.invoke(lambda folder=work: self.ready(release, folder))
            except Exception as exc:
                self.ui.invoke(lambda error=str(exc): self.download_failed(error))
        threading.Thread(target=worker, name='Sidro: Update download', daemon=True).start()

    def download_failed(self, error):
        self.busy = False
        self.progress.blockSignals(True)
        self.progress.close()
        if not self.cancelled.is_set():
            QMessageBox.warning(self.parent(), 'Sidro updates', f'The update was not installed. Sidro will keep running.\n\n{error}')

    def ready(self, release, work):
        self.progress.blockSignals(True)
        self.progress.close()
        self.busy = False
        if self.cancelled.is_set():
            return
        if self.tasks_active():
            QMessageBox.information(self.parent(), 'Sidro updates', 'A task started during download. Finish it and check for updates again when ready.')
            return
        answer = QMessageBox.question(self.parent(), 'Restart to update Sidro',
            f"Sidro {release['version']} is downloaded and verified.\nClose Sidro and install it now?\nUnsaved changes will not be saved.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        try:
            # Check parent-directory write access before closing the application.
            with tempfile.TemporaryFile(dir=config.APP_DIR.parent):
                pass
            helper = work / 'SidroUpdater.exe'
            shutil.copy2(config.APP_DIR / 'SidroUpdater.exe', helper)
            job = {'target': str(config.APP_DIR.resolve()), 'pid': os.getpid(), 'sha256': release['asset']['sha256']}
            job_path = work / 'job.json'
            job_path.write_text(json.dumps(job), encoding='utf-8')
            subprocess.Popen([str(helper), str(job_path)], cwd=work, creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as exc:
            QMessageBox.warning(self.parent(), 'Sidro updates', f'Could not start the updater. Sidro will keep running.\n\n{exc}')
            return
        self.ui.stop()


def check_for_updates():
    controller = getattr(QApplication.instance(), 'sidro_updates', None)
    if controller is not None:
        controller.check(manual=True)

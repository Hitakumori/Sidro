"""Standalone helper for replacing a portable Sidro installation.

Runs outside the installation directory, validates/extracts the update and
coordinates replacement/restart. The release builder packages this module
as SidroUpdater.exe alongside the main application.
"""
import ctypes
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile

from update_core import verify_archive


def extract_release(archive, destination):
    total = 0
    seen = set()
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            path = PurePosixPath(item.filename)
            parts = path.parts
            if not parts or path.is_absolute() or ".." in parts or "\\" in item.filename:
                raise ValueError("Unsafe path in update archive.")
            for part in parts:
                if ':' in part or part.rstrip(' .') != part or part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}:
                    raise ValueError("Invalid Windows path in update archive.")
            if any(part.lower() in ("user_data", ".env", ".git", "du.bin", "daily_usage.bin", "patreon_auth.bin", "support_reminder.json", "account_state.json", "app_settings.json") for part in parts):
                raise ValueError("Release contains personal runtime data.")
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("Update archives cannot contain symbolic links.")
            key = '/'.join(parts).casefold()
            if key in seen:
                raise ValueError("Duplicate file in update archive.")
            seen.add(key)
            total += item.file_size
            if total > 3 * 1024**3 or len(seen) > 30000:
                raise ValueError("Update archive is too large.")
            target = destination.joinpath(*parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(item) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
    roots = [destination] if (destination / 'Sidro.exe').is_file() else [p for p in destination.iterdir() if p.is_dir() and (p / 'Sidro.exe').is_file()]
    if len(roots) != 1 or not (roots[0] / 'SidroUpdater.exe').is_file() or not (roots[0] / '_internal').is_dir():
        raise ValueError("Release must contain Sidro.exe, SidroUpdater.exe, and _internal.")
    return roots[0]


def wait_for_exit(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.WaitForSingleObject(handle, 120000) != 0:
            raise RuntimeError("Sidro has not closed. No application files were changed.")
    finally:
        kernel.CloseHandle(handle)


def install_release(stage, target, backup, restart):
    # Both directory renames stay within the verified installation parent.
    for folder in (target, stage, backup):
        if folder.is_symlink():
            raise ValueError("Update paths cannot be symbolic links.")
    if backup.parent.resolve() != target.parent.resolve() or backup.exists():
        raise ValueError("Invalid rollback directory.")
    target.rename(backup)
    try:
        stage.rename(target)
        restart(target / 'Sidro.exe')
    except Exception:
        if target.exists():
            target.rename(stage)
        backup.rename(target)
        raise


def restart_and_check(exe, work):
    ready = work / 'ready'
    ready.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment['SIDRO_UPDATE_SESSION'] = work.name
    process = subprocess.Popen([str(exe)], cwd=exe.parent, env=environment)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if ready.exists():
            return
        if process.poll() is not None:
            raise RuntimeError('The new Sidro build exited before opening its dashboard.')
        time.sleep(0.2)
    process.terminate()
    process.wait(timeout=15)
    raise RuntimeError('The new Sidro build did not finish starting; restoring the previous build.')


def main():
    target = None
    job = None
    try:
        job_path = Path(sys.argv[1]).resolve()
        job = json.loads(job_path.read_text(encoding='utf-8'))
        target = Path(job['target']).resolve()
        if target == target.parent or not (target / 'Sidro.exe').is_file() or target == Path(sys.executable).resolve().parent:
            raise ValueError("Invalid Sidro installation directory.")
        archive = job_path.parent / 'release.zip'
        verify_archive(archive, job['sha256'])
        staging = Path(tempfile.mkdtemp(prefix='.Sidro-update-', dir=target.parent))
        stage = extract_release(archive, staging)
        wait_for_exit(int(job['pid']))
        # Preserve legacy portable data, including its original credentials.
        if (target / 'user_data').is_dir():
            shutil.copytree(target / 'user_data', stage / 'user_data')
        if (target / '.env').is_file():
            shutil.copy2(target / '.env', stage / '.env')
        backup = target.parent / (target.name + '-previous-' + staging.name.removeprefix('.Sidro-update-'))
        (job_path.parent / 'recovery.json').write_text(json.dumps({'target': str(target), 'backup': str(backup), 'stage': str(stage)}), encoding='utf-8')
        install_release(stage, target, backup, lambda exe: restart_and_check(exe, job_path.parent))
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(None, f'Sidro update could not be installed: {exc}\n\nYour AppData settings were preserved. The previous application is retained.', 'Sidro update', 0x10)
        if target is not None and (target / 'Sidro.exe').is_file():
            try:
                if job is not None:
                    wait_for_exit(int(job['pid']))
                subprocess.Popen([str(target / 'Sidro.exe')], cwd=target)
            except Exception:
                pass


if __name__ == '__main__':
    main()

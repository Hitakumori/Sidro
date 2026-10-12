"""Updater checks using fixtures; never replace a real installation."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import requests
import update_core as core
import updater_helper as helper


class Response:
    def __init__(self, data=None, status=200, chunks=()):
        self.data = data
        self.status_code = status
        self.chunks = chunks
        self.url = 'https://release-assets.githubusercontent.com/file'
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self):
        if self.status_code >= 400: raise requests.HTTPError(str(self.status_code))
    def json(self): return self.data
    def iter_content(self, *args): return iter(self.chunks)


def release(tag='v1.1.0'):
    return {'tag_name': tag, 'draft': False, 'prerelease': False, 'assets': [{
        'name': core.ASSET_NAME, 'size': 3, 'digest': 'sha256:' + 'a'*64,
        'browser_download_url': f'https://github.com/{core.REPOSITORY}/releases/download/{tag}/{core.ASSET_NAME}',
    }]}


class UpdatesTests(unittest.TestCase):
    def test_versions_are_numeric(self):
        self.assertGreater(core.version('v1.10.0'), core.version('1.9.0'))
        for value in ('v1.1.0-beta', 'garbage', '1.1'):
            with self.assertRaises(ValueError): core.version(value)

    def test_empty_repository_and_old_release(self):
        with patch.object(core.requests, 'get', return_value=Response(status=404)):
            self.assertIsNone(core.check_release('1.0.0'))
        with patch.object(core.requests, 'get', return_value=Response(release('v1.0.0'))):
            self.assertIsNone(core.check_release('1.0.0'))

    def test_stable_asset_and_prerelease(self):
        data = release()
        with patch.object(core.requests, 'get', return_value=Response(data)):
            self.assertIsNotNone(core.check_release('1.0.0')['asset'])
        data['prerelease'] = True
        with patch.object(core.requests, 'get', return_value=Response(data)):
            self.assertIsNone(core.check_release('1.0.0'))

    def test_versioned_release_asset(self):
        data = release()
        data['assets'][0]['name'] = 'Sidro-windows-v1.1.zip'
        data['assets'][0]['browser_download_url'] = f'https://github.com/{core.REPOSITORY}/releases/download/v1.1.0/Sidro-windows-v1.1.zip'
        with patch.object(core.requests, 'get', return_value=Response(data)):
            self.assertIsNotNone(core.check_release('1.0.0')['asset'])

    def test_unverified_or_wrong_host_asset_is_not_installable(self):
        for field, value in [('digest', None), ('browser_download_url', 'https://example.com/evil.zip')]:
            data = release()
            data['assets'][0][field] = value
            with patch.object(core.requests, 'get', return_value=Response(data)):
                self.assertIsNone(core.check_release('1.0.0')['asset'])

    def test_download_success_cancel_corruption_and_oversize(self):
        asset = {'url': 'https://github.com/file', 'size': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'release.zip'
            with patch.object(core.requests, 'get', return_value=Response(chunks=[b'a', b'bc'])):
                core.download_release(asset, path)
            self.assertEqual(path.read_bytes(), b'abc')
            for chunks, cancelled in [([b'abc'], lambda: True), ([b'xyz'], lambda: False), ([b'abcd'], lambda: False), ([b'ab'], lambda: False)]:
                with patch.object(core.requests, 'get', return_value=Response(chunks=chunks)):
                    with self.assertRaises(Exception): core.download_release(asset, path, cancelled=cancelled)
                self.assertFalse(path.exists())

    def test_valid_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root/'release.zip'
            with zipfile.ZipFile(archive, 'w') as output:
                for name in ('Sidro.exe', 'SidroUpdater.exe', '_internal/test.dll'):
                    output.writestr('Sidro/'+name, b'fixture')
            self.assertEqual(helper.extract_release(archive, root/'stage'), root/'stage'/'Sidro')

    def test_unsafe_archives(self):
        for name in ('../outside', '/absolute', 'Sidro/user_data/.env', 'Sidro/CON.txt', 'Sidro/file:stream', 'Sidro\\file'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root/'release.zip'
                with zipfile.ZipFile(archive, 'w') as output: output.writestr(name, b'x')
                with self.assertRaises(ValueError): helper.extract_release(archive, root/'stage')

    def test_rollback_and_backup(self):
        for fail in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target, stage, backup = [root / name for name in ('Sidro', 'stage', 'backup')]
                target.mkdir(); stage.mkdir()
                (target/'Sidro.exe').write_bytes(b'old')
                (stage/'Sidro.exe').write_bytes(b'new')
                def launch(exe):
                    self.assertEqual(exe, target/'Sidro.exe')
                    if fail: raise OSError('simulated launch failure')
                if fail:
                    with self.assertRaises(OSError): helper.install_release(stage, target, backup, launch)
                    self.assertEqual((target/'Sidro.exe').read_bytes(), b'old')
                else:
                    helper.install_release(stage, target, backup, launch)
                    self.assertEqual((target/'Sidro.exe').read_bytes(), b'new')
                    self.assertEqual((backup/'Sidro.exe').read_bytes(), b'old')

    def test_new_app_startup_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            exe = work/'Sidro.exe'
            class Started:
                def poll(self):
                    (work/'ready').write_text('1.1.0')
                    return None
            with patch.object(helper.subprocess, 'Popen', return_value=Started()):
                helper.restart_and_check(exe, work)
            class Failed:
                def poll(self): return 1
            with patch.object(helper.subprocess, 'Popen', return_value=Failed()):
                with self.assertRaises(RuntimeError): helper.restart_and_check(exe, work)


if __name__ == '__main__':
    unittest.main()

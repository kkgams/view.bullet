import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile
from legal_fixture import bind, rebind

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('release', ROOT / 'scripts/release.py')
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.root = self.work / 'repo'
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns('.git', '__pycache__', 'dist', 'node_modules'))
        bind(self.root, r)
        self.env = {f'APPROVED_{name}_SHA256': r.digest((self.root / name).read_bytes()) for name in ('LICENSE', 'NOTICE')}
        self.env['GITHUB_REPOSITORY'] = 'kkgams/' + json.loads((self.root / 'package.json').read_text())['name']
        self.patch = patch.dict(os.environ, self.env)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.out = self.work / 'out'

    def stage(self):
        r.stage(self.root, self.out, 'v0.1.0')

    def test_determinism_entire_assetset_and_individual_combined_install(self):
        self.stage()
        r.check(self.root, self.out, 'v0.1.0')
        second = self.work / 'second'
        r.stage(self.root, second, 'v0.1.0')
        for path in self.out.iterdir():
            self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())
        archives = sorted(self.out.glob('*.zip'))
        config = json.loads((self.root / 'release.json').read_text())
        self.assertEqual(len(archives), len(config['packages']))
        for index, path in enumerate(archives):
            destination = self.work / f'individual-{index}'
            r.install([path], self.out / 'SHA256SUMS', destination)
            with zipfile.ZipFile(path) as z:
                unit = json.loads(z.read('unit.json'))
                self.assertEqual(unit['host']['target'], '2.0.3')
                for name in unit['files']:
                    self.assertEqual((destination / name).read_bytes(), z.read(name))
                self.assertTrue(r.LEGAL <= set(unit['legal']))
                for name in set(unit['legal']) | {'README.md', 'unit.json'}:
                    self.assertEqual((destination / 'notices/project-units' / unit['name'] / name).read_bytes(), z.read(name))
        combined = self.work / 'combined'
        r.install(archives, self.out / 'SHA256SUMS', combined)
        expected = {target for p in config['packages'] for target in p['files'].values()}
        self.assertEqual({p.relative_to(combined).as_posix() for p in (combined / 'views').rglob('*') if p.is_file()}, expected)
        with self.assertRaises(ValueError):
            r.install(archives, self.out / 'SHA256SUMS', combined)

    def test_approval_evidence_canonical_and_version_gates(self):
        for key in self.env:
            with patch.dict(os.environ, {key: ''}):
                with self.assertRaises((ValueError, KeyError)):
                    r.payload(self.root, 'v0.1.0')
        with self.assertRaises(ValueError):
            r.payload(self.root, 'v00.1.0')
        p = next((self.root / 'src').rglob('*.js'))
        p.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.stage()

    def test_missing_extra_corrupt_candidate_no_source_fallback(self):
        self.stage()
        archive = next(self.out.glob('*.zip'))
        original = archive.read_bytes()
        archive.unlink()
        with self.assertRaises(ValueError):
            r.check(self.root, self.out, 'v0.1.0')
        archive.write_bytes(original + b'bad')
        with self.assertRaises(ValueError):
            r.check(self.root, self.out, 'v0.1.0')
        archive.write_bytes(original)
        (self.out / 'extra').write_text('bad')
        with self.assertRaises(ValueError):
            r.check(self.root, self.out, 'v0.1.0')
        with self.assertRaises(FileExistsError):
            self.stage()

    def test_unsafe_extraction_preflight_no_partial_writes(self):
        self.stage()
        path = next(self.out.glob('*.zip'))
        original = path.read_bytes()
        for unsafe in ('../escape.js', '/absolute.js', 'views/../escape.js', 'views\\escape.js'):
            data = io.BytesIO(original)
            with zipfile.ZipFile(data, 'a') as z:
                z.writestr(unsafe, b'bad')
            path.write_bytes(data.getvalue())
            sums = self.work / 'sums'
            sums.write_text(f'{r.digest(data.getvalue())}  {path.name}\n')
            target = self.work / 'unsafe'
            with self.assertRaises(ValueError):
                r.install([path], sums, target)
            self.assertFalse(target.exists())

    def test_collision_and_symlink_rejection(self):
        self.stage()
        archives = sorted(self.out.glob('*.zip'))
        target = self.work / 'duplicate'
        with self.assertRaises(ValueError):
            r.install([archives[0], archives[0]], self.out / 'SHA256SUMS', target)
        self.assertFalse(target.exists())
        target.mkdir()
        (target / 'views').symlink_to(self.work / 'external')
        with self.assertRaises(ValueError):
            r.install(archives, self.out / 'SHA256SUMS', target)

    def test_branch_and_api_fail_closed(self):
        with patch.object(r.subprocess, 'run'), patch.object(r.subprocess, 'check_output', side_effect=[b'a', b'b', b'a']), patch.dict(os.environ, {'GITHUB_SHA': 'a'}):
            with self.assertRaises(ValueError):
                r.branch('v0.1.0')
        for code in (401, 403, 500):
            with patch.object(r.urllib.request, 'urlopen', side_effect=urllib.error.HTTPError('url', code, 'error', {}, None)):
                with self.assertRaises(urllib.error.HTTPError):
                    r.absent(self.env['GITHUB_REPOSITORY'], 'v0.1.0', 'token')
        with patch.object(r.urllib.request, 'urlopen'):
            with self.assertRaises(ValueError):
                r.absent(self.env['GITHUB_REPOSITORY'], 'v0.1.0', 'token')

    def test_public_release_requires_immutable_complete_set(self):
        self.stage()
        for field, value in [('draft', True), ('prerelease', True), ('immutable', False), ('assets', [])]:
            document = dict(tag_name='v0.1.0', draft=False, prerelease=False, immutable=True, assets=[])
            document[field] = value
            response = io.BytesIO(json.dumps(document).encode())
            with patch.object(r.urllib.request, 'urlopen', return_value=response):
                with self.assertRaises(ValueError):
                    r.public(self.root, self.out, 'v0.1.0')

    def test_public_complete_bytes_and_canonical_urls(self):
        self.stage()
        repository = self.env['GITHUB_REPOSITORY']
        expected = {p.name: p.read_bytes() for p in self.out.iterdir()}
        assets = [{'name': name, 'state': 'uploaded', 'browser_download_url':
                   f'https://github.com/{repository}/releases/download/v0.1.0/{name}'} for name in expected]
        document = dict(tag_name='v0.1.0', draft=False, prerelease=False, immutable=True, assets=assets)
        def response(url, **kwargs):
            if not isinstance(url, str):
                return io.BytesIO(json.dumps(document).encode())
            return io.BytesIO(expected[url.rsplit('/', 1)[1]])
        with patch.object(r.urllib.request, 'urlopen', side_effect=response):
            r.public(self.root, self.out, 'v0.1.0')
            expected[next(iter(expected))] = b'changed'
            with self.assertRaises(ValueError):
                r.public(self.root, self.out, 'v0.1.0')

    def test_zip_duplicate_and_symlink_members(self):
        self.stage()
        path = next(self.out.glob('*.zip'))
        original = path.read_bytes()
        for symlink in (False, True):
            data = io.BytesIO(original)
            with zipfile.ZipFile(data, 'a') as z:
                if symlink:
                    item = zipfile.ZipInfo('views/link.js')
                    item.create_system = 3
                    item.external_attr = 0o120777 << 16
                    z.writestr(item, b'outside')
                else:
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore', UserWarning)
                        z.writestr('unit.json', b'{}')
            path.write_bytes(data.getvalue())
            sums = self.work / 'sums'
            sums.write_text(f'{r.digest(data.getvalue())}  {path.name}\n')
            with self.assertRaises(ValueError):
                r.install([path], sums, self.work / 'malicious')
            self.assertFalse((self.work / 'malicious').exists())

    def test_exact_inputs_and_schema_fail_closed(self):
        for name in ('src/added.js', 'scripts/added.js', 'LICENSES/added.txt', 'nested/build/added.js'):
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('unreviewed')
            with self.assertRaises(ValueError):
                r.payload(self.root, 'v0.1.0')
            p.unlink()
        original = json.loads((self.root / 'NOTICE-EVIDENCE.json').read_text())
        for key, value in [('permission_blockers', ['missing author permission']), ('schema', 2),
                           ('repository', 'view.wrong'), ('hash_exclusions', {}), ('notice_body_sha256', '0' * 64),
                           ('prepared_adaptations', [])]:
            proof = {**original, key: value}
            rebind(self.root, r, proof)
            with patch.dict(os.environ, {'APPROVED_NOTICE_SHA256': r.digest((self.root / 'NOTICE').read_bytes())}):
                with self.assertRaises(ValueError):
                    r.payload(self.root, 'v0.1.0')
        rebind(self.root, r, {'files_sha256': original['files_sha256']})
        with patch.dict(os.environ, {'APPROVED_NOTICE_SHA256': r.digest((self.root / 'NOTICE').read_bytes())}):
            with self.assertRaises(ValueError):
                r.payload(self.root, 'v0.1.0')

    def test_destination_root_and_source_ancestor_symlinks(self):
        self.stage()
        real = self.work / 'real'
        real.mkdir()
        link = self.work / 'link'
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            r.install(sorted(self.out.glob('*.zip')), self.out / 'SHA256SUMS', link)
        self.assertEqual(list(real.iterdir()), [])
        (self.root / 'nested').symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            r.payload(self.root, 'v0.1.0')

    def test_release_branch_approval_truth_table(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        condition = "startsWith(github.ref, 'refs/tags/v') || (github.ref == 'refs/heads/release' && (vars.LICENSE_SHA256 != '' || vars.NOTICE_SHA256 != ''))"
        self.assertEqual(workflow.count('if: ' + condition), 3)
        for license_value, notice_value, stages, succeeds in [('', '', False, False),
                (self.env['APPROVED_LICENSE_SHA256'], '', True, False),
                ('', self.env['APPROVED_NOTICE_SHA256'], True, False),
                ('bad', 'bad', True, False),
                (self.env['APPROVED_LICENSE_SHA256'], self.env['APPROVED_NOTICE_SHA256'], True, True)]:
            self.assertEqual(bool(license_value or notice_value), stages)
            if stages:
                with patch.dict(os.environ, {'APPROVED_LICENSE_SHA256': license_value, 'APPROVED_NOTICE_SHA256': notice_value}):
                    if succeeds:
                        r.payload(self.root, 'v0.1.0')
                    else:
                        with self.assertRaises(ValueError):
                            r.payload(self.root, 'v0.1.0')

    def test_policy_and_draft_before_publish(self):
        repository = self.env['GITHUB_REPOSITORY']
        for enabled in (True, False):
            with patch.object(r.urllib.request, 'urlopen', side_effect=[
                    io.BytesIO(b'{"private": false, "visibility": "public"}'),
                    io.BytesIO(json.dumps({'enabled': enabled}).encode())]):
                if enabled:
                    r.policy(repository, 'token')
                else:
                    with self.assertRaises(ValueError):
                        r.policy(repository, 'token')
        for code in (401, 403, 404, 500):
            with patch.object(r.urllib.request, 'urlopen', side_effect=urllib.error.HTTPError('url', code, 'error', {}, None)):
                with self.assertRaises(urllib.error.HTTPError):
                    r.policy(repository, 'token')
        self.stage()
        expected = {p.name: p.read_bytes() for p in self.out.iterdir()}
        assets = [{'id': index, 'name': name, 'state': 'uploaded', 'url':
                   f'https://api.github.com/repos/{repository}/releases/assets/{index}'}
                  for index, name in enumerate(sorted(expected))]
        document = dict(tag_name='v0.1.0', draft=True, prerelease=False, assets=assets)
        def response(request, **kwargs):
            if '/releases/assets/' not in request.full_url:
                return io.BytesIO(json.dumps(document).encode())
            asset = assets[int(request.full_url.rsplit('/', 1)[1])]
            return io.BytesIO(expected[asset['name']])
        with patch.object(r.urllib.request, 'urlopen', side_effect=response):
            r.draft(self.root, self.out, 'v0.1.0', 'token')
            expected[assets[0]['name']] = b'corrupt upload'
            with self.assertRaises(ValueError):
                r.draft(self.root, self.out, 'v0.1.0', 'token')
        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        self.assertIn('scripts/publication.py publish', workflow)
        self.assertIn('RELEASE_POLICY_TOKEN: ${{ secrets.RELEASE_POLICY_TOKEN }}', workflow)
        publisher = (ROOT / 'scripts/publication.py').read_text()
        self.assertLess(publisher.index('draft_bytes(repository'), publisher.index("gh('release', 'edit'"))
        self.assertIn("'--draft', '--verify-tag'", publisher)

    def test_workflow_gates(self):
        workflow = (ROOT / '.github/workflows/release.yml').read_text()
        for gate in ('needs: candidate', "vars.LICENSE_SHA256 != '' || vars.NOTICE_SHA256 != ''", 'publication.py policy', 'publication.py publish', 'release.py branch', 'release.py absent', 'release.py check', 'GH_TOKEN: ${{ github.token }}'):
            self.assertIn(gate, workflow)
        self.assertNotIn('--clobber', workflow)
        self.assertNotIn('npm publish', workflow)


if __name__ == '__main__':
    unittest.main()

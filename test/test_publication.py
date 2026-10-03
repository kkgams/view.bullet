"""Isolated publisher regressions: every subprocess and HTTP call is intercepted."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('publication', SCRIPTS / 'publication.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.out = self.root / 'dist/candidate'
        self.out.mkdir(parents=True)
        self.repo = 'kkgams/view.example'
        self.tag = 'v0.1.1'
        self.sha = 'a' * 40
        self.obj = 'b' * 40
        for name, document in {
            'package.json': {'name': 'view.example', 'version': '0.1.1'},
            'release.json': {'repository': 'view.example', 'packages': [{'name': 'view.one'}, {'name': 'view.two'}]},
            'canonical.json': {'view.example': ['view.one', 'view.two']},
        }.items():
            (self.root / name).write_text(json.dumps(document))
        self.expected = {'view.one-0.1.1.zip': b'one', 'view.two-0.1.1.zip': b'two', 'SHA256SUMS': b'sums'}
        for name, data in self.expected.items():
            (self.out / name).write_bytes(data)
        self.repository = {'full_name': self.repo, 'private': False, 'visibility': 'public'}
        self.document = {'id': 17, 'url': f'{p.API}/repos/{self.repo}/releases/17',
                         'tag_name': self.tag, 'draft': True, 'prerelease': False,
                         'immutable': False, 'published_at': None, 'body': 'generated notes', 'assets': []}
        for index, (name, data) in enumerate(self.expected.items(), 1):
            self.document['assets'].append({
                'id': index, 'name': name, 'state': 'uploaded', 'size': len(data),
                'digest': 'sha256:' + p.release.digest(data), 'download_count': 0,
                'url': f'{p.API}/repos/{self.repo}/releases/assets/{index}',
                'browser_download_url': f'https://github.com/{self.repo}/releases/download/untagged-xyz/{name}',
            })
        self.calls, self.requests = [], []
        self.created = self.published = False
        self.existing = []
        self.delayed = 0
        self.clock = 0
        self.before_download = lambda: None
        self.before_public_download = lambda: None
        self.before_edit = lambda: None
        self.start_patch(patch.dict(os.environ, {
            'GITHUB_REPOSITORY': self.repo, 'GITHUB_SHA': self.sha,
            'GH_TOKEN': 'contents-secret', 'RELEASE_POLICY_TOKEN': 'admin-secret',
            'GH_DEBUG': 'api',
        }, clear=True))
        self.runner = self.start_patch(patch.object(p.subprocess, 'run', side_effect=self.run_command))
        self.opener = self.start_patch(patch.object(p.urllib.request, 'urlopen', side_effect=self.urlopen))
        self.check = self.start_patch(patch.object(p.release, 'check'))
        self.branch = self.start_patch(patch.object(p.release, 'branch'))
        self.start_patch(patch.object(p.time, 'monotonic', side_effect=lambda: self.clock))
        self.start_patch(patch.object(p.time, 'sleep', side_effect=self.sleep))

    def start_patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def sleep(self, duration):
        self.clock += duration

    def run_command(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if args[0] == 'git':
            command = args[3:]
            if command == ['remote', 'get-url', 'origin']:
                output = f'https://github.com/{self.repo}.git'
            elif command[:2] == ['cat-file', '-t']:
                output = 'tag'
            elif command[0] == 'rev-parse':
                output = self.sha if command[1].endswith('^{commit}') else self.obj
            elif command[0] == 'ls-remote':
                output = f'{self.obj}\trefs/tags/{self.tag}\n{self.sha}\trefs/tags/{self.tag}^{{}}'
            else:
                self.fail('unexpected git call: ' + repr(args))
            return subprocess.CompletedProcess(args, 0, output.encode(), b'')
        self.assertEqual(args[0], 'gh')
        self.assertEqual(kwargs['env']['GH_TOKEN'], 'contents-secret')
        self.assertNotIn('RELEASE_POLICY_TOKEN', kwargs['env'])
        self.assertEqual(kwargs['env']['GH_DEBUG'], '')
        self.assertNotIn('admin-secret', repr(args))
        self.assertNotIn('contents-secret', repr(args))
        output = b''
        if args[1] == 'api':
            self.assertIn('--method', args)
            self.assertIn('GET', args)
            self.assertIn('Cache-Control: no-cache', args)
            endpoint = args[2]
            if endpoint == f'repos/{self.repo}':
                document = self.repository
            elif endpoint.endswith('/releases?per_page=100'):
                self.assertIn('--paginate', args)
                self.assertIn('--slurp', args)
                documents = self.existing
                if self.created:
                    if self.delayed:
                        self.delayed -= 1
                    else:
                        documents = [self.document]
                document = [[], documents]  # Include an empty earlier page.
            elif endpoint.endswith('/releases/17'):
                document = self.document
            elif endpoint.endswith('/releases/17/assets?per_page=100'):
                document = [self.document['assets']]
            else:
                self.fail('unexpected API call: ' + endpoint)
            output = json.dumps(document).encode()
        elif args[1:3] == ['release', 'create']:
            self.assertFalse(self.created)
            self.assertIn('--draft', args)
            self.assertIn('--verify-tag', args)
            self.assertIn('--generate-notes', args)
            self.assertIn('--latest=false', args)
            self.assertEqual(set(args[4:4 + len(self.expected)]), {str(self.out / n) for n in self.expected})
            self.created = True
        elif args[1:3] == ['release', 'download']:
            self.before_download()
            folder = Path(args[args.index('--dir') + 1])
            for name, data in self.expected.items():
                (folder / name).write_bytes(data)
        elif args[1:3] == ['release', 'edit']:
            self.before_edit()
            self.assertIn('--draft=false', args)
            self.assertIn('--latest=false', args)
            self.published = True
            self.document['draft'] = False
            self.document['immutable'] = True
            self.document['published_at'] = '2026-10-03T09:00:00Z'
            for asset in self.document['assets']:
                asset['browser_download_url'] = f'https://github.com/{self.repo}/releases/download/{self.tag}/{asset["name"]}'
        else:
            self.fail('unexpected gh write/command: ' + repr(args))
        return subprocess.CompletedProcess(args, 0, output, b'')

    def urlopen(self, request, **kwargs):
        self.requests.append(request)
        url = request.full_url
        if request.get_header('Authorization') is not None:
            self.assertEqual(request.get_header('Authorization'), 'Bearer admin-secret')
            if url == f'{p.API}/repos/{self.repo}':
                document = self.repository
            elif url == f'{p.API}/repos/{self.repo}/immutable-releases':
                document = {'enabled': True}
            else:
                self.fail('policy token used for non-policy endpoint')
        elif '/releases/tags/' in url:
            self.assertTrue(self.published)
            document = self.document
        elif '/releases/17/assets?' in url:
            document = self.document['assets']
        elif '/releases/download/' in url:
            self.before_public_download()
            return io.BytesIO(self.expected[url.rsplit('/', 1)[1]])
        else:
            self.fail('unexpected HTTP endpoint: ' + url)
        return io.BytesIO(json.dumps(document).encode())

    def publish(self):
        p.publish(self.root, self.out, self.tag)

    def writes(self):
        return [args[2] for args, _ in self.calls if args[:2] == ['gh', 'release'] and args[2] in ('create', 'edit')]

    def test_full_draft_first_flow_with_delayed_visibility(self):
        self.delayed = 3
        self.publish()
        self.assertEqual(self.clock, 3)
        self.assertEqual(self.writes(), ['create', 'edit'])
        self.assertEqual(self.check.call_count, 2)
        self.assertEqual(self.branch.call_count, 2)
        self.assertTrue(self.published)
        self.assertEqual(sum('/immutable-releases' in r.full_url for r in self.requests), 2)

    def test_default_is_read_only_policy_and_needs_no_contents_token(self):
        del os.environ['GH_TOKEN']
        p.main(['--root', str(self.root)])
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.requests), 2)

    def test_policy_never_uses_contents_token_as_fallback(self):
        del os.environ['RELEASE_POLICY_TOKEN']
        with self.assertRaises(KeyError):
            p.policy(self.repo)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.calls, [])

    def test_policy_403_is_fatal_not_absence(self):
        error = urllib.error.HTTPError('https://api.github.com/policy', 403, 'Forbidden', {}, io.BytesIO())
        self.addCleanup(error.close)
        self.opener.side_effect = error
        with self.assertRaises(urllib.error.HTTPError):
            self.publish()
        self.assertEqual(self.writes(), [])

    def test_policy_disabled_private_wrong_repo_or_malformed(self):
        for document in ({'enabled': False}, {'enabled': 1}, {}, [], {'enabled': 'true'}):
            with self.subTest(document=document), patch.object(p, 'http', side_effect=[self.repository, document]):
                with self.assertRaises((ValueError, KeyError)):
                    p.policy(self.repo)
        for field, value in [('private', True), ('private', 0), ('visibility', 'private'), ('full_name', 'other/repo')]:
            document = dict(self.repository, **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                p.repository_state(document, self.repo)

    def test_existing_draft_or_public_release_refused(self):
        for draft in (True, False):
            self.existing = [dict(self.document, draft=draft)]
            with self.subTest(draft=draft), self.assertRaisesRegex(ValueError, 'existing tag release'):
                self.publish()
        self.assertEqual(self.writes(), [])

    def test_api_errors_and_bad_pagination_never_mean_absence(self):
        for output in (b'{}', b'[]', b'[{}]', b'null', b'not json'):
            with self.subTest(output=output), patch.object(p, 'gh', return_value=output):
                with self.assertRaises((ValueError, TypeError)):
                    p.matching(self.repo, self.tag)
        with patch.object(p, 'gh', side_effect=ValueError('403')):
            with self.assertRaises(ValueError):
                p.await_created(self.repo, self.tag)
        self.assertEqual(self.clock, 0)

    def test_ambiguous_and_malformed_listings_fatal(self):
        for documents in ([self.document, self.document],
                          [self.document, dict(self.document, id=18)],
                          [dict(self.document, draft='true')], [dict(self.document, id=True)], [{}]):
            with self.subTest(documents=documents), patch.object(p, 'api', return_value=documents):
                with self.assertRaises((ValueError, KeyError)):
                    p.matching(self.repo, self.tag)

    def test_empty_list_only_poll_times_out_without_recreating(self):
        self.delayed = 100
        with self.assertRaisesRegex(ValueError, '30 seconds'):
            self.publish()
        self.assertEqual(self.clock, 30)
        self.assertEqual(self.writes(), ['create'])

    def test_create_failure_is_not_retried(self):
        original = self.run_command
        def fail(args, **kwargs):
            if args[1:3] == ['release', 'create']:
                self.calls.append((args, kwargs))
                return subprocess.CompletedProcess(args, 1, b'', b'sensitive stderr')
            return original(args, **kwargs)
        self.runner.side_effect = fail
        with self.assertRaisesRegex(ValueError, 'gh command failed'):
            self.publish()
        self.assertEqual(self.writes(), ['create'])

    def test_draft_untagged_url_accepted_public_untagged_rejected(self):
        p.validate(self.document, self.repo, self.tag, self.expected, True)
        document = dict(self.document, draft=False, immutable=True, published_at='2026-10-03T09:00:00Z')
        with self.assertRaisesRegex(ValueError, 'public asset URL'):
            p.validate(document, self.repo, self.tag, self.expected, False)

    def test_bad_asset_metadata_and_state(self):
        mutations = [('id', 0), ('id', True), ('url', 'https://evil.example/asset'),
                     ('state', 'new'), ('size', 999), ('size', True),
                     ('digest', None), ('digest', 'sha256:' + '0' * 64), ('name', 'unexpected')]
        for field, value in mutations:
            document = copy.deepcopy(self.document)
            document['assets'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                p.validate(document, self.repo, self.tag, self.expected, True)
        for change in ('missing', 'extra', 'duplicate'):
            document = copy.deepcopy(self.document)
            if change == 'missing':
                document['assets'].pop()
            elif change == 'extra':
                document['assets'].append(copy.deepcopy(document['assets'][0]))
            else:
                document['assets'][1]['id'] = document['assets'][0]['id']
            with self.subTest(change=change), self.assertRaises(ValueError):
                p.validate(document, self.repo, self.tag, self.expected, True)

    def test_bad_release_state_cannot_publish(self):
        for field, value in [('draft', False), ('prerelease', True), ('immutable', True),
                             ('id', 0), ('url', 'https://evil.example/release'), ('tag_name', 'v9.0.0')]:
            document = dict(self.document, **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                p.validate(document, self.repo, self.tag, self.expected, True)

    def test_draft_bytes_mismatch_blocks_edit(self):
        def corrupt():
            self.expected['SHA256SUMS'] = b'bad'
        # The orchestrator retains its original candidate bytes before downloads.
        self.before_download = corrupt
        with self.assertRaisesRegex(ValueError, 'draft bytes'):
            self.publish()
        self.assertEqual(self.writes(), ['create'])

    def test_all_metadata_stable_except_download_count(self):
        def count():
            for asset in self.document['assets']:
                asset['download_count'] += 1
        self.before_download = count
        self.before_public_download = count
        self.publish()
        self.assertEqual(self.writes(), ['create', 'edit'])

    def test_draft_metadata_change_blocks_edit(self):
        self.before_download = lambda: self.document.update(body='changed')
        with self.assertRaisesRegex(ValueError, 'draft changed'):
            self.publish()
        self.assertEqual(self.writes(), ['create'])

    def test_public_bytes_and_metadata_changes_fail(self):
        self.before_public_download = lambda: self.expected.update(SHA256SUMS=b'bad')
        with self.assertRaisesRegex(ValueError, 'public asset bytes'):
            self.publish()
        self.assertEqual(self.writes(), ['create', 'edit'])

    def test_public_metadata_change_after_downloads_fails(self):
        self.before_public_download = lambda: self.document.update(body='changed')
        with self.assertRaisesRegex(ValueError, 'public metadata changed'):
            self.publish()

    def test_branch_change_or_candidate_failure_blocks_writes(self):
        self.branch.side_effect = ValueError('changed branch/workflow SHA')
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.writes(), [])
        self.branch.side_effect = None
        self.check.side_effect = ValueError('candidate audit mismatch')
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.writes(), [])

    def test_branch_moves_after_create_blocks_edit(self):
        self.branch.side_effect = [None, ValueError('branch moved')]
        with self.assertRaisesRegex(ValueError, 'branch moved'):
            self.publish()
        self.assertEqual(self.writes(), ['create'])

    def test_tag_identity_changes_after_create_blocks_edit(self):
        def change():
            self.obj = 'c' * 40
        self.before_download = change
        with self.assertRaisesRegex(ValueError, 'source/tag identity changed'):
            self.publish()
        self.assertEqual(self.writes(), ['create'])

    def test_remote_lightweight_or_different_annotation_rejected(self):
        for output in (f'{self.sha}\trefs/tags/{self.tag}',
                       f'{"c" * 40}\trefs/tags/{self.tag}\n{self.sha}\trefs/tags/{self.tag}^{{}}'):
            original = self.run_command
            def remote(args, **kwargs):
                if 'ls-remote' in args:
                    return subprocess.CompletedProcess(args, 0, output.encode(), b'')
                return original(args, **kwargs)
            with self.subTest(output=output), patch.object(p.subprocess, 'run', side_effect=remote):
                with self.assertRaisesRegex(ValueError, 'remote annotated tag'):
                    self.publish()
        self.assertEqual(self.writes(), [])

    def test_contents_token_required_before_any_write(self):
        del os.environ['GH_TOKEN']
        with self.assertRaises(KeyError):
            self.publish()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.requests, [])

    def test_policy_recheck_failure_leaves_draft_without_edit(self):
        original = self.urlopen
        probes = 0
        def fail(request, **kwargs):
            nonlocal probes
            if request.full_url.endswith('/immutable-releases'):
                probes += 1
                if probes == 2:
                    error = urllib.error.HTTPError(request.full_url, 403, 'Forbidden', {}, io.BytesIO())
                    self.addCleanup(error.close)
                    raise error
            return original(request, **kwargs)
        self.opener.side_effect = fail
        with self.assertRaises(urllib.error.HTTPError):
            self.publish()
        self.assertEqual(self.writes(), ['create'])

    def test_edit_failure_never_retries_or_repairs(self):
        original = self.run_command
        def fail(args, **kwargs):
            if args[1:3] == ['release', 'edit']:
                self.calls.append((args, kwargs))
                return subprocess.CompletedProcess(args, 1, b'', b'failure')
            return original(args, **kwargs)
        self.runner.side_effect = fail
        with self.assertRaisesRegex(ValueError, 'gh command failed'):
            self.publish()
        self.assertEqual(self.writes(), ['create', 'edit'])
        self.assertFalse(self.published)

    def test_listing_detail_and_asset_listing_changes_are_fatal(self):
        self.created = True
        original = p.api
        for suffix in ('/releases/17', '/releases/17/assets?per_page=100'):
            def changed(repository, path='', **kwargs):
                document = original(repository, path, **kwargs)
                if path == suffix:
                    if type(document) is list:
                        document[0]['label'] = 'changed'
                    else:
                        document['body'] = 'changed'
                return document
            with self.subTest(suffix=suffix), patch.object(p, 'api', side_effect=changed):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    p.draft_snapshot(self.repo, self.tag, self.expected)

    def test_candidate_change_after_download_blocks_edit(self):
        self.before_download = lambda: (self.out / 'SHA256SUMS').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'candidate changed'):
            self.publish()
        self.assertEqual(self.writes(), ['create'])

    def test_noncanonical_origin_and_local_lightweight_tag_rejected(self):
        original = self.run_command
        for command, output in [('remote', 'https://github.com/evil/repo.git'), ('cat-file', 'commit')]:
            def changed(args, **kwargs):
                if args[0] == 'git' and args[3] == command:
                    return subprocess.CompletedProcess(args, 0, output.encode(), b'')
                return original(args, **kwargs)
            with self.subTest(command=command), patch.object(p.subprocess, 'run', side_effect=changed):
                with self.assertRaises(ValueError):
                    self.publish()
        self.assertEqual(self.writes(), [])

    def test_version_repository_and_family_are_dynamic_and_strict(self):
        self.assertEqual(p.identity(self.root), (self.repo, 'v0.1.1'))
        with self.assertRaises(ValueError):
            p.identity(self.root, 'v0.1.0')
        os.environ['GITHUB_REPOSITORY'] = 'kkgams/view.other'
        with self.assertRaises(ValueError):
            p.identity(self.root)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()

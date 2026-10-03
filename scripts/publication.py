#!/usr/bin/env python3
"""Fail-closed, draft-first CI publisher. Reads may be repeated; writes never are.

RELEASE_POLICY_TOKEN needs fine-grained Administration:read. GH_TOKEN is the
workflow contents:write token. No existing release (including a draft) is resumed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

import release

API = 'https://api.github.com'
HEADERS = {'Accept': 'application/vnd.github+json', 'Cache-Control': 'no-cache',
           'Pragma': 'no-cache', 'X-GitHub-Api-Version': '2022-11-28'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def token(name):
    value = os.environ[name]
    require(bool(value.strip()), 'required token is empty: ' + name)
    return value


def identity(root, tag=None):
    package = json.loads(release.read_owned(root, 'package.json'))
    config = json.loads(release.read_owned(root, 'release.json'))
    canonical = json.loads(release.read_owned(root, 'canonical.json'))
    name = config['repository']
    require(type(name) is str and re.fullmatch(r'[a-z0-9.-]+', name), 'invalid repository')
    require(name == package['name'] and name in canonical, 'noncanonical repository')
    require([p['name'] for p in config['packages']] == canonical[name], 'noncanonical family')
    version = package['version']
    require(type(version) is str and re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version), 'invalid version')
    expected = 'v' + version
    require(tag is None or tag == expected, 'tag/version mismatch')
    repository = 'kkgams/' + name
    require(os.environ['GITHUB_REPOSITORY'] == repository, 'noncanonical workflow repository')
    return repository, expected


def http(url, credential=None, binary=False):
    headers = dict(HEADERS)
    if credential is not None:
        headers['Authorization'] = 'Bearer ' + credential
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read() if binary else json.load(response)


def repository_state(document, repository):
    require(type(document) is dict and document['full_name'] == repository and
            document['private'] is False and document['visibility'] == 'public',
            'repository must be canonical and public')


def policy(repository):
    credential = token('RELEASE_POLICY_TOKEN')
    repository_state(http(f'{API}/repos/{repository}', credential), repository)
    document = http(f'{API}/repos/{repository}/immutable-releases', credential)
    require(type(document) is dict and document['enabled'] is True,
            'repository immutable releases must be enabled')


def gh(*args, timeout=60):
    env = dict(os.environ, GH_TOKEN=token('GH_TOKEN'), GH_HOST='github.com',
               GH_PROMPT_DISABLED='1', GH_DEBUG='')
    # The administrative credential must never enter a gh subprocess.
    env.pop('RELEASE_POLICY_TOKEN', None)
    result = subprocess.run(['gh', *map(str, args)], env=env, capture_output=True, timeout=timeout)
    require(result.returncode == 0, 'gh command failed (exit ' + str(result.returncode) + '): ' + ' '.join(map(str, args)))
    return result.stdout


def api(repository, suffix='', paginated=False, timeout=60):
    args = ['api', f'repos/{repository}{suffix}', '--method', 'GET', '--hostname', 'github.com']
    for name, value in HEADERS.items():
        args += ['--header', f'{name}: {value}']
    if paginated:
        args += ['--paginate', '--slurp']
    document = json.loads(gh(*args, timeout=timeout))
    if not paginated:
        require(type(document) is dict, 'malformed API object')
        return document
    require(type(document) is list and bool(document) and
            all(type(page) is list for page in document), 'malformed paginated API list')
    return [item for page in document for item in page]


def positive(value):
    return type(value) is int and value > 0


def matching(repository, tag, timeout=60):
    documents = api(repository, '/releases?per_page=100', paginated=True, timeout=timeout)
    ids = set()
    for document in documents:
        require(type(document) is dict and positive(document['id']) and
                type(document['tag_name']) is str and type(document['draft']) is bool and
                type(document['prerelease']) is bool, 'malformed release listing')
        require(document['id'] not in ids, 'ambiguous release listing')
        ids.add(document['id'])
    matches = [d for d in documents if d['tag_name'] == tag]
    require(len(matches) <= 1, 'ambiguous tag release')
    return matches


def await_created(repository, tag):
    deadline = time.monotonic() + 30
    while True:
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'created draft not visible within 30 seconds')
        matches = matching(repository, tag, timeout=remaining)
        require(time.monotonic() < deadline, 'created draft not visible within 30 seconds')
        if matches:
            return matches[0]
        time.sleep(min(1, deadline - time.monotonic()))


def stable(document):
    """Retain all metadata, except the expected download counter mutation."""
    if type(document) is dict:
        return {k: stable(v) for k, v in document.items() if k != 'download_count'}
    if type(document) is list:
        return [stable(v) for v in document]
    return document


def validate(document, repository, tag, expected, draft):
    require(type(document) is dict and positive(document['id']) and
            document['url'] == f'{API}/repos/{repository}/releases/{document["id"]}' and
            document['tag_name'] == tag and document['draft'] is draft and
            document['prerelease'] is False and type(document['immutable']) is bool,
            'invalid release identity/state')
    require(document['immutable'] is (not draft), 'unexpected immutability state')
    if not draft:
        require(type(document['published_at']) is str and bool(document['published_at']), 'missing publication timestamp')
    assets = document['assets']
    require(type(assets) is list and all(type(a) is dict for a in assets), 'malformed assets')
    require(len(assets) == len(expected) and {a['name'] for a in assets} == set(expected),
            'complete assetset mismatch')
    ids = set()
    for asset in assets:
        data = expected[asset['name']]
        require(positive(asset['id']) and asset['id'] not in ids, 'invalid/duplicate asset ID')
        ids.add(asset['id'])
        require(asset['url'] == f'{API}/repos/{repository}/releases/assets/{asset["id"]}' and
                asset['state'] == 'uploaded' and type(asset['size']) is int and
                asset['size'] == len(data) and asset['digest'] == 'sha256:' + release.digest(data),
                'asset URL/state/size/digest mismatch')
        require(type(asset['browser_download_url']) is str, 'malformed browser download URL')
        # GitHub draft URLs may use untagged-<id>; only the API URL binds drafts.
        if not draft:
            url = f'https://github.com/{repository}/releases/download/{tag}/' + urllib.parse.quote(asset['name'], safe='')
            require(asset['browser_download_url'] == url, 'noncanonical public asset URL')
    return stable(document)


def draft_snapshot(repository, tag, expected):
    matches = matching(repository, tag)
    require(len(matches) == 1, 'draft missing')
    listed = matches[0]
    document = api(repository, '/releases/' + str(listed['id']))
    snapshot = validate(document, repository, tag, expected, True)
    require(validate(listed, repository, tag, expected, True) == snapshot, 'listing/detail changed')
    assets = api(repository, f'/releases/{document["id"]}/assets?per_page=100', paginated=True)
    require(stable(assets) == stable(document['assets']), 'asset listing/detail changed')
    return snapshot


def draft_bytes(repository, tag, expected):
    with tempfile.TemporaryDirectory(prefix='gams-publication-') as folder:
        gh('release', 'download', tag, '--repo', repository, '--dir', folder)
        actual = {p.name: p.read_bytes() for p in Path(folder).iterdir()}
        require(actual == expected, 'authenticated draft bytes/assetset mismatch')


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    require(result.returncode == 0, 'git identity check failed')
    return result.stdout.decode().strip()


def source(root, out, tag):
    release.check(root, out, tag)  # Includes release.payload and complete legal/source audit.
    repository, _ = identity(root, tag)
    origin = git(root, 'remote', 'get-url', 'origin')
    allowed = {f'https://github.com/{repository}', f'git@github.com:{repository}',
               f'ssh://git@github.com/{repository}'}
    require(origin in allowed | {url + '.git' for url in allowed}, 'noncanonical git origin')
    previous = Path.cwd()
    try:
        os.chdir(root)
        release.branch(tag)
    finally:
        os.chdir(previous)
    ref = 'refs/tags/' + tag
    require(git(root, 'cat-file', '-t', ref) == 'tag', 'tag must be annotated')
    local = git(root, 'rev-parse', ref)
    commit = git(root, 'rev-parse', ref + '^{commit}')
    require(re.fullmatch('[0-9a-f]{40}', local) and re.fullmatch('[0-9a-f]{40}', commit), 'invalid tag object')
    lines = git(root, 'ls-remote', '--exit-code', 'origin', ref, ref + '^{}').splitlines()
    require(sorted(lines) == sorted([f'{local}\t{ref}', f'{commit}\t{ref}^{{}}']),
            'remote annotated tag identity mismatch')
    return local, commit


def public_snapshot(repository, tag, expected):
    document = http(f'{API}/repos/{repository}/releases/tags/{tag}')
    snapshot = validate(document, repository, tag, expected, False)
    assets = []
    page = 1
    while True:
        batch = http(f'{API}/repos/{repository}/releases/{document["id"]}/assets?per_page=100&page={page}')
        require(type(batch) is list, 'malformed public asset listing')
        assets.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    require(stable(assets) == stable(document['assets']), 'public asset listing/detail changed')
    return snapshot


def publish(root, out, tag):
    repository, tag = identity(root, tag)
    token('GH_TOKEN')
    token('RELEASE_POLICY_TOKEN')
    bound = source(root, out, tag)
    expected = {p.name: p.read_bytes() for p in out.iterdir()}
    repository_state(api(repository), repository)
    policy(repository)
    require(not matching(repository, tag), 'existing tag release; refusing publication')
    gh('release', 'create', tag, *[out / name for name in sorted(expected)],
       '--repo', repository, '--draft', '--verify-tag', '--latest=false',
       '--title', repository.split('/')[1] + ' ' + tag, '--generate-notes')
    await_created(repository, tag)
    draft = draft_snapshot(repository, tag, expected)
    draft_bytes(repository, tag, expected)
    require(draft_snapshot(repository, tag, expected) == draft, 'draft changed during downloads')
    require(source(root, out, tag) == bound, 'source/tag identity changed')
    require({p.name: p.read_bytes() for p in out.iterdir()} == expected, 'candidate changed')
    require(identity(root, tag) == (repository, tag), 'publication identity changed')
    repository_state(api(repository), repository)
    policy(repository)
    require(draft_snapshot(repository, tag, expected) == draft, 'draft changed before publication')
    gh('release', 'edit', tag, '--repo', repository, '--draft=false', '--latest=false')
    document = public_snapshot(repository, tag, expected)
    require(document['id'] == draft['id'], 'public release identity changed')
    for asset in document['assets']:
        require(http(asset['browser_download_url'], binary=True) == expected[asset['name']],
                'public asset bytes mismatch')
    require(public_snapshot(repository, tag, expected) == document, 'public metadata changed during downloads')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('policy', 'publish'), default='policy')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--out', type=Path, default=Path('dist/candidate'))
    parser.add_argument('--tag')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    repository, tag = identity(root, args.tag)
    if args.command == 'policy':
        policy(repository)
    else:
        out = args.out if args.out.is_absolute() else root / args.out
        publish(root, out, tag)


if __name__ == '__main__':
    main()

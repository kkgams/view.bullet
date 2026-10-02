#!/usr/bin/env python3
"""Stdlib-only deterministic Project Unit staging and independent verification."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import urllib.error
import urllib.request
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def approved(root):
    for name in ('LICENSE', 'NOTICE'):
        expected = os.environ['APPROVED_' + name + '_SHA256']
        if not re.fullmatch('[0-9a-f]{64}', expected) or digest((root / name).read_bytes()) != expected:
            raise ValueError(f'{name} owner-approved digest missing or mismatched')
    if 'PROVENANCE_PENDING' in (root / 'NOTICE').read_text():
        raise ValueError('source-bound legal evidence pending')


def safe_path(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or str(path) != value or '\\' in value:
        raise ValueError('unsafe path: ' + value)
    return path


def read_owned(root, value):
    safe_path(value)
    local = root / value
    if not local.resolve().is_relative_to(root) or any(p.is_symlink() for p in [local, *local.parents]):
        raise ValueError('symlink/escaping input: ' + value)
    return local.read_bytes()


LEGAL = {'LICENSE', 'NOTICE', 'NOTICE-EVIDENCE.json', 'THIRD-PARTY-REVIEW.md', 'LICENSING.md'}
EXCLUSIONS = {
    'NOTICE-EVIDENCE.json': 'self-reference: this manifest cannot hash itself',
    'NOTICE': 'contains the digest of NOTICE-EVIDENCE.json; independently hashed in returned record',
    'SOURCE.json': 'written by orchestrator after audit; must bind NOTICE and evidence independently',
}
EVIDENCE_TYPES = dict(schema=int, repository=str, scope=str, notice_body_sha256=str,
    limits=list, findings=list, permission_blockers=list, distribution_contract=dict,
    collection=dict, audit_generator_sha256=str, audit_input_sha256=dict,
    wasi_vendor_files_sha256=dict, source_comparison_exclusions=dict,
    source_files_sha256=dict, prepared_exact_source_origins=dict, prepared_adaptations=dict,
    upstream_terms=list, host_runtime_imports=list, shipped_host_source_files=list,
    hash_exclusions=dict, files_sha256=dict)


def no_symlinks(path):
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('symlink path: ' + str(path))


def verify_evidence(proof, notice, repository, config):
    evidence = json.loads(proof)
    if set(evidence) != set(EVIDENCE_TYPES) or any(type(evidence[k]) is not t for k, t in EVIDENCE_TYPES.items()):
        raise ValueError('malformed audit schema')
    if evidence['schema'] != 1 or evidence['repository'] != repository or evidence['hash_exclusions'] != EXCLUSIONS:
        raise ValueError('audit schema/identity/exclusions mismatch')
    for key in ('limits', 'findings', 'permission_blockers', 'host_runtime_imports', 'shipped_host_source_files'):
        if any(type(value) is not str for value in evidence[key]):
            raise ValueError('malformed audit list: ' + key)
    if evidence['permission_blockers']:
        raise ValueError('explicit redistribution permission blockers')
    exclusions = evidence['source_comparison_exclusions']
    if set(exclusions) != {'directories', 'suffixes', 'generated_suffix', 'legal_evidence_preserved'} or exclusions['legal_evidence_preserved'] is not True or exclusions['generated_suffix'] != '.mjir.json':
        raise ValueError('malformed source comparison policy')
    for key in ('directories', 'suffixes'):
        if type(exclusions[key]) is not list or any(type(value) is not str for value in exclusions[key]):
            raise ValueError('malformed source comparison exclusions')
    for path, origins in evidence['prepared_exact_source_origins'].items():
        safe_path(path)
        if type(origins) is not list or any(type(origin) is not str for origin in origins):
            raise ValueError('malformed source origins')
    for path, adaptation in evidence['prepared_adaptations'].items():
        safe_path(path)
        if type(adaptation) is not dict or set(adaptation) not in ({'comparison_candidates', 'status'}, {'comparison_candidates', 'status', 'source_sha256'}) or type(adaptation['comparison_candidates']) is not list or type(adaptation['status']) is not str:
            raise ValueError('malformed adaptation')
        if any(type(source) is not str for source in adaptation['comparison_candidates']):
            raise ValueError('malformed adaptation source')
        if 'source_sha256' in adaptation:
            if len(adaptation['comparison_candidates']) != 1 or adaptation['source_sha256'] != evidence['source_files_sha256'][adaptation['comparison_candidates'][0]]:
                raise ValueError('adaptation source digest mismatch')
    for term in evidence['upstream_terms']:
        if type(term) is not dict or set(term) != {'authority', 'file', 'sha256'} or any(type(v) is not str for v in term.values()):
            raise ValueError('malformed upstream term')
        safe_path(term['file'])
        if not re.fullmatch('[0-9a-f]{64}', term['sha256']) or evidence['files_sha256'].get('LICENSES/' + term['file']) != term['sha256']:
            raise ValueError('upstream terms are not source-bound')
    for key in ('files_sha256', 'source_files_sha256', 'audit_input_sha256', 'wasi_vendor_files_sha256'):
        for path, value in evidence[key].items():
            safe_path(path)
            if type(value) is not str or not re.fullmatch('[0-9a-f]{64}', value):
                raise ValueError('invalid audit hash map')
    for key in ('notice_body_sha256', 'audit_generator_sha256'):
        if not re.fullmatch('[0-9a-f]{64}', evidence[key]):
            raise ValueError('invalid audit digest')
    suffix = f'NOTICE-EVIDENCE.json SHA-256: {digest(proof)}\n'.encode()
    if not notice.endswith(suffix) or digest(notice[:-len(suffix)]) != evidence['notice_body_sha256']:
        raise ValueError('NOTICE body/evidence binding mismatch')
    distribution = evidence['distribution_contract']
    if distribution != {'files': {s: t for p in config['packages'] for s, t in p['files'].items()}, 'packages': config['packages']}:
        raise ValueError('audit distribution contract mismatch')
    if set(evidence['files_sha256']) & set(EXCLUSIONS):
        raise ValueError('audit hashes excluded paths')
    return evidence['files_sha256']


def input_set(root):
    """Only exact root generated/cache paths are exempt; nested names are inputs."""
    paths = set()
    def walk(folder):
        for p in folder.iterdir():
            rel = p.relative_to(root).as_posix()
            # Git internals may contain worktree links; never distribution inputs.
            if rel == '.git':
                continue
            no_symlinks(p)
            if rel in ('dist/candidate', 'scripts/__pycache__', 'test/__pycache__'):
                continue
            if p.is_dir():
                walk(p)
            elif p.is_file():
                if rel not in EXCLUSIONS:
                    paths.add(rel)
            else:
                raise ValueError('nonregular source input: ' + rel)
    walk(root)
    return paths


def payload(root, tag):
    no_symlinks(root)
    root = root.resolve()
    package = json.loads(read_owned(root, 'package.json'))
    config = json.loads(read_owned(root, 'release.json'))
    canonical = json.loads(read_owned(root, 'canonical.json'))
    repository = config['repository']
    if repository != package['name'] or repository not in canonical:
        raise ValueError('canonical repository mismatch')
    if 'GITHUB_REPOSITORY' in os.environ and os.environ['GITHUB_REPOSITORY'] != 'kkgams/' + repository:
        raise ValueError('canonical workflow repository mismatch')
    if [p['name'] for p in config['packages']] != canonical[repository]:
        raise ValueError('canonical complete package set mismatch')
    version = package['version']
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version) or tag != 'v' + version:
        raise ValueError('tag/version mismatch')
    if package['license'] != 'Apache-2.0' or package['private'] is not True:
        raise ValueError('invalid publication policy')
    approved(root)
    proof = read_owned(root, 'NOTICE-EVIDENCE.json')
    evidence = verify_evidence(proof, read_owned(root, 'NOTICE'), repository, config)
    if input_set(root) != set(evidence):
        raise ValueError('audited exact input set changed')
    for path, expected in evidence.items():
        if digest(read_owned(root, path)) != expected:
            raise ValueError('audited input changed: ' + path)
    closure = {p.relative_to(root).as_posix() for folder in ('src', 'upstream')
               for p in (root / folder).rglob('*') if p.is_file()}
    if closure != {p for p in evidence if p.startswith(('src/', 'upstream/'))}:
        raise ValueError('evidence source closure mismatch')
    for required in ('release.json', 'canonical.json', 'SOURCE-CLOSURE.json', 'HOST-CONTRACT.json'):
        if required not in evidence:
            raise ValueError('missing source-bound manifest evidence: ' + required)
    outputs, deployed, owned = {}, set(), set()
    for unit in config['packages']:
        if unit['kind'] != 'view' or not unit['files'] or set(unit['sha256']) != set(unit['files']):
            raise ValueError('invalid per-package manifest')
        files, hashes = {}, {}
        for source, target in unit['files'].items():
            safe_path(target)
            if not source.startswith('src/' + unit['name'] + '/') or not target.startswith('views/'):
                raise ValueError('invalid package deployment scope')
            if source in owned or target in deployed:
                raise ValueError('package deployment collision')
            owned.add(source)
            deployed.add(target)
            data = read_owned(root, source)
            if digest(data) != unit['sha256'][source] or source not in evidence:
                raise ValueError('package hash/evidence mismatch: ' + source)
            files[target] = data
            hashes[target] = digest(data)
        if unit['entry'] not in files:
            raise ValueError('entry missing')
        legal = LEGAL | {name for name in evidence if name.startswith('LICENSES/')}
        if not (LEGAL - set(EXCLUSIONS)) <= set(evidence):
            raise ValueError('missing legal audit inputs')
        for name in legal | {'README.md'}:
            files[name] = read_owned(root, name)
        manifest = dict(legal={name: digest(files[name]) for name in sorted(legal)}, name=unit['name'], kind='view', tag=unit['tag'], version=version, entry=unit['entry'],
                        files=hashes, host={'target': '2.0.3', 'imports': config['hostImports'][unit['name']],
                        'note': 'GUI consumption remains a release gate.'})
        files['unit.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
        outputs[unit['name'] + '-' + version + '.zip'] = files
    if owned != {p for p in closure if p.startswith('src/')}:
        raise ValueError('unowned/missing package source closure')
    return outputs


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as z:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, data)
    return output.getvalue()


def stage(root, out, tag):
    no_symlinks(out)
    outputs = payload(root, tag)
    out.mkdir(parents=True, exist_ok=False)
    checksums = ''
    for name, files in sorted(outputs.items()):
        data = archive(files)
        (out / name).write_bytes(data)
        checksums += f'{digest(data)}  {name}\n'
    (out / 'SHA256SUMS').write_text(checksums)


def check(root, out, tag):
    no_symlinks(out)
    for path in out.iterdir():
        no_symlinks(path)
    outputs = payload(root, tag)
    if {p.name for p in out.iterdir()} != set(outputs) | {'SHA256SUMS'}:
        raise ValueError('missing/extra entire expected assetset')
    checksums = ''
    for name, files in sorted(outputs.items()):
        data = (out / name).read_bytes()
        checksums += f'{digest(data)}  {name}\n'
        if data != archive(files):
            raise ValueError('candidate content/metadata mismatch: ' + name)
    if (out / 'SHA256SUMS').read_text() != checksums:
        raise ValueError('checksum mismatch')


def install(archives, checksums, destination):
    """Validate all bytes/paths/collisions before any Project writes; no extractall."""
    no_symlinks(destination)
    destination = destination.resolve()
    expected = {}
    for line in checksums.read_text().splitlines():
        value, name = line.split('  ')
        safe_path(name)
        if name in expected or not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('invalid checksums')
        expected[name] = value
    writes = {}
    for path in archives:
        data = path.read_bytes()
        if digest(data) != expected[path.name]:
            raise ValueError('archive checksum mismatch')
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            if len(names) != len(set(names)):
                raise ValueError('duplicate ZIP paths')
            for item in z.infolist():
                safe_path(item.filename)
                if item.is_dir() or (item.external_attr >> 16) & 0o170000 != 0o100000:
                    raise ValueError('nonregular ZIP entry')
            unit = json.loads(z.read('unit.json'))
            if unit['kind'] != 'view' or not re.fullmatch(r'view\.[a-z0-9-]+', unit['name']):
                raise ValueError('invalid installed identity')
            legal = set(unit['legal'])
            if not LEGAL <= legal or any(name not in LEGAL and not name.startswith('LICENSES/') for name in legal):
                raise ValueError('missing/invalid legal closure')
            if unit['entry'] not in unit['files'] or set(names) != set(unit['files']) | legal | {'README.md', 'unit.json'}:
                raise ValueError('invalid ZIP manifest')
            proof = z.read('NOTICE-EVIDENCE.json')
            evidence = json.loads(proof)
            audited = verify_evidence(proof, z.read('NOTICE'), evidence['repository'],
                                      {'packages': evidence['distribution_contract']['packages']})
            if unit['name'] not in {p['name'] for p in evidence['distribution_contract']['packages']}:
                raise ValueError('installed identity absent from audit')
            if legal != LEGAL | {name for name in evidence['files_sha256'] if name.startswith('LICENSES/')}:
                raise ValueError('incomplete audited legal closure')
            for name in legal:
                if digest(z.read(name)) != unit['legal'][name] or (name not in EXCLUSIONS and digest(z.read(name)) != audited[name]):
                    raise ValueError('legal bytes mismatch')
            for name in names:
                content = z.read(name)
                if name in unit['files']:
                    if not name.startswith('views/') or digest(content) != unit['files'][name]:
                        raise ValueError('deployment hash/path mismatch')
                    target = name
                else:
                    target = 'notices/project-units/' + unit['name'] + '/' + name
                safe_path(target)
                local = destination / target
                if target in writes or local.exists() or local.is_symlink() or any(p.is_symlink() for p in local.parents):
                    raise ValueError('installation collision: ' + target)
                writes[target] = content
    for target, content in writes.items():
        local = destination / target
        local.parent.mkdir(parents=True, exist_ok=True)
        with local.open('xb') as output:
            output.write(content)


def branch(tag):
    package = json.loads((Path(__file__).resolve().parents[1] / 'package.json').read_text())
    if tag != 'v' + package['version']:
        raise ValueError('tag/version mismatch')
    subprocess.run(['git', 'fetch', '--no-tags', 'origin', 'refs/heads/release'], check=True)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD']).strip()
    release = subprocess.check_output(['git', 'rev-parse', 'FETCH_HEAD']).strip()
    tagged = subprocess.check_output(['git', 'rev-parse', tag + '^{commit}']).strip()
    if head != release or head != tagged or head.decode() != os.environ['GITHUB_SHA']:
        raise ValueError('tag must match current release branch HEAD and workflow SHA')


def absent(repository, tag, token):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or not re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', tag):
        raise ValueError('invalid release identity')
    request = urllib.request.Request(
        f'https://api.github.com/repos/{repository}/releases/tags/{tag}',
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(request, timeout=30):
            pass
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 404:
            return
        raise
    raise ValueError('immutable release already exists; refusing overwrite')


def policy(repository, token):
    """Read-only repository policy gate; API/access failures are fatal."""
    for endpoint in ('', '/immutable-releases'):
        request = urllib.request.Request(f'https://api.github.com/repos/{repository}{endpoint}',
            headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            document = json.load(response)
        if endpoint == '' and (document['private'] or document['visibility'] != 'public'):
            raise ValueError('repository must be public')
        if endpoint and document['enabled'] is not True:
            raise ValueError('repository immutable releases must be enabled')


def draft(root, out, tag, token):
    """Verify every uploaded byte before freezing the draft into a public release."""
    check(root, out, tag)
    repository = 'kkgams/' + json.loads((root / 'release.json').read_text())['repository']
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'}
    request = urllib.request.Request(f'https://api.github.com/repos/{repository}/releases/tags/{tag}', headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    if release['tag_name'] != tag or release['draft'] is not True or release['prerelease']:
        raise ValueError('expected stable unpublished draft')
    expected = {p.name: p.read_bytes() for p in out.iterdir()}
    assets = release['assets']
    if len(assets) != len(expected) or {a['name'] for a in assets} != set(expected):
        raise ValueError('draft complete assetset mismatch')
    for asset in assets:
        if asset['state'] != 'uploaded' or asset['url'] != f'https://api.github.com/repos/{repository}/releases/assets/{asset["id"]}':
            raise ValueError('invalid draft asset')
        request = urllib.request.Request(asset['url'], headers={**headers, 'Accept': 'application/octet-stream'})
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.read() != expected[asset['name']]:
                raise ValueError('draft asset bytes mismatch')


def public(root, out, tag):
    """Unauthenticated public API and exact-byte consumption of the complete assetset."""
    check(root, out, tag)
    repository = 'kkgams/' + json.loads((root / 'release.json').read_text())['repository']
    request = urllib.request.Request(f'https://api.github.com/repos/{repository}/releases/tags/{tag}',
                                     headers={'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    if release['tag_name'] != tag or release['draft'] or release['prerelease'] or release['immutable'] is not True:
        raise ValueError('release must be public stable and immutable')
    assets = release['assets']
    expected = {p.name: p.read_bytes() for p in out.iterdir()}
    if len(assets) != len(expected) or {a['name'] for a in assets} != set(expected):
        raise ValueError('public complete assetset mismatch')
    for asset in assets:
        if asset['state'] != 'uploaded':
            raise ValueError('public asset incomplete')
        url = asset['browser_download_url']
        if not url.startswith(f'https://github.com/{repository}/releases/download/{tag}/'):
            raise ValueError('noncanonical public asset URL')
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        if data != expected[asset['name']]:
            raise ValueError('public asset bytes mismatch')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['stage', 'check', 'branch', 'absent', 'install', 'public', 'policy', 'draft'])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--out', type=Path, default=Path('dist/candidate'))
    parser.add_argument('--tag', required=True)
    parser.add_argument('--archives', type=Path, nargs='+')
    parser.add_argument('--checksums', type=Path)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    if args.command in ('stage', 'check'):
        globals()[args.command](args.root, args.out, args.tag)
    elif args.command == 'branch':
        branch(args.tag)
    elif args.command == 'install':
        install(args.archives, args.checksums, args.destination)
    elif args.command == 'public':
        public(args.root, args.out, args.tag)
    elif args.command == 'policy':
        policy(os.environ['GITHUB_REPOSITORY'], os.environ['GH_TOKEN'])
    elif args.command == 'draft':
        draft(args.root, args.out, args.tag, os.environ['GH_TOKEN'])
    else:
        absent(os.environ['GITHUB_REPOSITORY'], args.tag, os.environ['GH_TOKEN'])

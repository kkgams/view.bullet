"""Schema-complete synthetic audit for offline release protocol tests, NOT approval."""
import json


def bind(root, release):
    # Protocol tests retain a synthetic 0.1.0 fixture independently of release version.
    package = json.loads((root / 'package.json').read_text())
    package['version'] = '0.1.0'
    (root / 'package.json').write_text(json.dumps(package, indent=2) + '\n')
    config = json.loads((root / 'release.json').read_text())
    (root / 'LICENSES').mkdir(exist_ok=True)
    (root / 'LICENSES/fixture-Apache-2.0.txt').write_bytes((root / 'LICENSE').read_bytes())
    (root / 'THIRD-PARTY-REVIEW.md').write_text('PRIVATE TEST FIXTURE: synthetic review; no provenance claim.\n')
    (root / 'LICENSING.md').write_text('PRIVATE TEST FIXTURE: preserve LICENSES full terms.\n')
    body = b'PRIVATE TEST FIXTURE ONLY; not owner approval. Full terms: LICENSES/.\n'
    sources = {p.relative_to(root).as_posix(): release.digest(p.read_bytes()) for p in (root / 'upstream').rglob('*') if p.is_file()}
    proof = dict(schema=1, repository=config['repository'], scope='synthetic test source inputs',
        notice_body_sha256=release.digest(body), limits=['Private fixture; no release permission'],
        findings=['Synthetic legal closure preservation test'], permission_blockers=[],
        distribution_contract={'files': {s: t for p in config['packages'] for s, t in p['files'].items()}, 'packages': config['packages']},
        collection={'snapshot': 'synthetic fixture'}, audit_generator_sha256=release.digest(b'fixture generator'),
        audit_input_sha256={'fixture.txt': release.digest(b'fixture')}, wasi_vendor_files_sha256={},
        source_comparison_exclusions={'directories': [], 'suffixes': [], 'generated_suffix': '.mjir.json', 'legal_evidence_preserved': True},
        source_files_sha256=sources, prepared_exact_source_origins={}, prepared_adaptations={},
        upstream_terms=[{'authority': 'synthetic fixture: repository LICENSE', 'file': 'fixture-Apache-2.0.txt', 'sha256': release.digest((root / 'LICENSE').read_bytes())}],
        host_runtime_imports=sorted({v for values in config['hostImports'].values() for v in values}),
        shipped_host_source_files=[], hash_exclusions=release.EXCLUSIONS,
        files_sha256={name: release.digest((root / name).read_bytes()) for name in release.input_set(root)})
    rebind(root, release, proof, body)


def rebind(root, release, proof, body=None):
    if body is None:
        body = (root / 'NOTICE').read_bytes().rsplit(b'NOTICE-EVIDENCE.json SHA-256: ', 1)[0]
    raw = (json.dumps(proof, indent=2, sort_keys=True) + '\n').encode()
    (root / 'NOTICE-EVIDENCE.json').write_bytes(raw)
    (root / 'NOTICE').write_bytes(body + f'NOTICE-EVIDENCE.json SHA-256: {release.digest(raw)}\n'.encode())

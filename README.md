# view.bullet

One separately installable ZIP per View, never npm/OCI. Host contract: GAMS 2.0.3.
Run npm test (Node 24, Python 3 stdlib). release.json binds package identities, deployment maps and hashes.
Absolute imports enumerated in release.json are required Host-owned runtime/util/widget APIs; missing APIs, config or schemas are bugs, not optional fallbacks.
Keep Project Config ids/tags and URLs as deployed. view-markdow spelling is deliberately unchanged.
Blend 1D retains views/view-animation-blend-spaces.js; independently installed 2D requires views/view-animation-blend-space-2d.js in Project Config. No HTML/class behavior is rewritten.
Install using scripts/release.py install (verified checksums), retain metadata under notices/project-units/<package>. Refuse all overwrite collisions.
Dependencies (plugins, sibling Views, UI Services) remain Project configured; static Host imports are documented, not vendored.
HOST-CONTRACT.json includes required dynamic widget paths and known legacy schema limits.
GUI validation pending: Bullet simulation controls, preview and persistence. No blanket E2E claim.

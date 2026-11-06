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

## Release authorization

A pushed matching version tag authorizes publication from any branch. No manual
checksum approval variables are required; source/evidence and artifact checks remain
strict. See `PUBLISHING.md`.

## Local commands

Requires Node.js 24+, Python 3, Make, curl, tar and unzip. No npm install needed.

```sh
npm test
make open                      # current local gams.json in tools/gams.app
make serve-docs                 # http://localhost:8080; DOCS_PORT overrides
make validate-docs
make docs                       # portable wiki under build/site/
```

The View source loads directly from `src/`; no compilation is required.
`make tools/gams.app` downloads the released macOS Apple Silicon Host only when
absent, preserving its notices. `make open` explicitly sets `GAMS_APP_CWD` to
this repository; no sibling checkout is required. Keep macOS security checks intact.
Published dependencies are pinned in `gams.json` and cached under ignored
`gams_modules/`. First launch needs network access.

The wiki uses pinned justgook/wiki v1.4.0. GitHub Pages runs `make docs` on every
push to `release`, independently of Unit tags. Enable **Settings → Pages → Source
→ GitHub Actions**. `build-docs` remains an alias. Native GUI and hosted workflow
execution are separate verification steps.

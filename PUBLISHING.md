# Owner-controlled publication

Preparation performs no Git, network publication, npm or OCI operations. Each
repository releases its complete canonical package set at one strict SemVer tag.

1. Review SOURCE-CLOSURE.json, all src/ and upstream/ bytes and the complete
   release.json mapping. Add actual source-bound legal evidence and upstream terms;
   replace PROVENANCE_PENDING using the full remaining_audit schema v1.
   NOTICE-EVIDENCE.json binds the exact complete repository input set, repository,
   distribution contract, exclusions, legal terms and NOTICE body. Explicit
   permission blockers are fatal.
   Only exact root SOURCE.json/NOTICE/evidence, .git, dist/candidate and Python
   caches scripts/__pycache__/test/__pycache__ are excluded after preparation.
   New source, scripts or legal files require a separately authorized audit refresh;
   this policy migration does not refresh evidence.
   NOTICE must end with `NOTICE-EVIDENCE.json SHA-256: <digest>`.
   Preserve source-only license snippets; do not interpret Apache-2.0 as relicensing
   upstream code. Parent owns this evidence, remotes and final Git setup.
2. Run npm test (Node 24/Python 3), then separately consume each package in GAMS
   Host 2.0.3. Required runtime, utility and widget import paths are enumerated in
   release.json. No missing-import/config/schema recovery is supported. Services,
   plugins and sibling Views must be configured by the Project. Source preservation
   and syntax checks are not GUI E2E evidence.
3. A pushed matching version tag authorizes publication from any branch, without
   manual checksum approval variables. Enable GitHub immutable releases and public
   visibility. Never move a pushed tag.
4. Release-branch CI stages and checks candidates without approval variables.
   Stage and check cover the entire expected ZIP assetset plus SHA256SUMS with
   deterministic bytes; no missing-output source fallback. Every ZIP has independent
   unit.json, deployment hashes, retained metadata and Host target 2.0.3.
5. Tag CI repeats tests, canonical repository, version, tag/workflow commit and
   artifact checks. The tag need not match a release-branch HEAD.
   Both jobs check out the event commit explicitly with full history: pinned checkout
   v5 tag-mode can locally replace an annotated tag with its peeled commit. Explicit
   commit checkout preserves remote tag objects; strict annotation/remote identity
   checks remain required (never ref repair or acceptance of lightweight tags).
   Existing releases (including drafts) and API failures block publication. Public
   creation first requires canonical public repository metadata. The automatic
   GitHub token handles metadata and release/content operations; no administrative token or
   immutable-policy endpoint preflight is required. Post-publication verification
   still requires immutable release metadata.
   scripts/publication.py publishes draft-first: bounded no-cache visibility polling
   after one successful create (never repeats writes), canonical authenticated asset
   API identities/digests/downloaded bytes and stable metadata. Draft browser links
   may use untagged-*; public links must use the exact version tag. Source/annotated
   tag, candidate bytes and canonical public visibility are rechecked before
   freeze. Published exact bytes/complete assetset/stable immutable metadata are then
   checked anonymously. Failed drafts/tags require a NEW version, never overwrite,
   repair or retag. Normal tag publication does not require the local owner publisher.

Local rehearsal (no approval environment variables required; evidence must match):

```
python3 scripts/release.py stage --tag v0.2.0
python3 scripts/release.py check --tag v0.2.0
python3 scripts/release.py install --tag v0.2.0 --archives dist/candidate/view.NAME-0.2.0.zip --checksums dist/candidate/SHA256SUMS --destination /path/to/project
```

Installation checks selected archive checksums, ZIP members, manifests, hashes and
all deployment collisions before writing; it never overwrites. Notices go under
notices/project-units/<package>/, never over Project README/LICENSE. Every ZIP and
individual installation retains LICENSE, NOTICE, NOTICE-EVIDENCE.json,
THIRD-PARTY-REVIEW.md, LICENSING.md and all audited LICENSES files, even for siblings. Siblings and
all 26 packages may be installed together with no duplicate deployment files.

Blend separation: original class bodies/HTML are retained byte-equivalently; each
entry removes only the opposite customElements registration. Original complete
source is retained under upstream/ (not deployed). 1D retains historical
views/view-animation-blend-spaces.js; 2D now uses views/view-animation-blend-space-2d.js.
The former shared URL cannot select two independent single-View modules without a
collision; Projects configuring 2D must update its URL, not its tag/config id.
view-markdow.js and view-markdow tag retain their historical spelling.

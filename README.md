# view.bullet

BulletML simulation preview and controls for [GAMS](https://github.com/kkgams/gams). Host runtime APIs, utilities and widgets remain external dependencies.

| View | Source | Screenshot | Original work |
| --- | --- | --- | --- |
| `view-bullet` | [views/view-bullet.js](src/view.bullet/views/view-bullet.js) | Not available | GAMS contributors |

GAMS contributions: [Apache-2.0](LICENSE); retain [NOTICE](NOTICE) and [licensing scope](LICENSING.md).

## Development

Requires Node.js 24+, Python 3, Make, curl, tar, unzip and zip. No compilation or package installation is required.

| Command | Purpose |
| --- | --- |
| `make open` | Open local gams.json in the released macOS Apple Silicon Host. |
| `make check` | Check View JavaScript syntax. |
| `make package` | Build stable-name View ZIPs and SHA256SUMS under build.nosync/. |
| `make docs` | Build pinned wiki v1.4.0 under build.nosync/site/. |
| `make serve-docs DOCS_PORT=8081` | Preview the wiki on an independent port. |
| `make validate-docs` | Validate wiki content and links. |
| `make clean` | Remove all of `build.nosync/`, `tools/` and `gams_modules/`, without ownership markers; refuse tracked files or symlinks in deletion paths before deleting anything. |

The local Project opens `bulletml.json`, a minimal empty BulletML document. Editing and saving changes that real file. Simulation controls, preview and persistence need native GUI verification. Missing required Host imports, config or schemas are errors.

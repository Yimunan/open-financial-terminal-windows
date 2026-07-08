# Building the macOS app from source

This folder holds the tooling used to freeze Open Financial Terminal into a native macOS
`.app` and package it as a DMG:

| File | Purpose |
|---|---|
| `oft-macos.spec` | PyInstaller spec — bundles the FastAPI backend + qhfi engine + built SPA into `Open Financial Terminal.app` (adds PyWebView/pyobjc, an `.icns` icon, and an ATS localhost exception). |
| `rthook_env.py` | PyInstaller runtime hook — bakes in **no** secrets; generates a per-machine encryption key on first run. Users set their own LLM key in the in-app Settings. |
| `oft.icns` | App icon. |
| `build-macos.sh` | End-to-end build script (clone → deps → freeze → DMG). |

## ⚠️ Prerequisite: the qhfi data/model packages

The app imports `qhfi.data.*` and `qhfi.models.*` at load time. **The public
[quant-hedge-fund-incubator](https://github.com/Yimunan/quant-hedge-fund-incubator) repo does not
contain those two packages** — its `.gitignore` has

```
data/
models/
reports/
```

which are meant for the regenerable data lake, but they **also match the source packages**
`src/qhfi/data/` and `src/qhfi/models/`, so git silently excluded them on push
(`git check-ignore src/qhfi/data/base.py` → `data/`). A freshly cloned qhfi therefore can't import,
and the app won't build.

**Fix (in the qhfi repo):** anchor those ignore rules to the repo root so they don't match the
source tree, then commit the packages:

```gitignore
# was:  data/   models/   reports/     (matches src/qhfi/data, src/qhfi/models too)
# use:  /data/  /models/  /reports/    (only the top-level lake/artifacts)
```
```sh
git rm -r --cached . && git add . && git commit -m "fix: stop ignoring src/qhfi/{data,models}"
```

Until the upstream repo is fixed, build from a qhfi checkout that includes `src/qhfi/data` and
`src/qhfi/models`. The **pre-built DMG in Releases already contains them** — most people should just
use that.

## Requirements
- macOS 12+ on **Apple Silicon** (arm64)
- [`uv`](https://github.com/astral-sh/uv) (`brew install uv`) — used for a clean Python 3.11
- Node 18+ (to build the SPA) — or copy a prebuilt `frontend/dist`

## Build
```sh
./build-macos.sh
```
The result is `dist-mac/Open Financial Terminal.app` and `OpenFinancialTerminal.dmg`.

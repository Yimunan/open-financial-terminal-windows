# Releasing a new version (maintainer runbook)

How to go from changed sources → rebuilt installer → GitHub release. This repo is **standalone**:
the full application source is vendored in-tree (`open-financial-terminal/` +
`quant-hedge-fund-incubator/` as siblings), and the canonical build is the GitHub Actions workflow
[`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml) on a `windows-latest`
runner — no local Windows machine required.

## 0. What kind of change is it?

| Changed | What to do |
|---|---|
| anything under `open-financial-terminal/**` or `quant-hedge-fund-incubator/**` | commit + push, then dispatch the workflow (it rebuilds SPA + freeze + installer every run) |
| docs / screenshots / README only | commit + push this repo — no build needed |
| upstream source drift (new work in the source repos) | re-vendor the snapshots, update the provenance note in README §standalone, then build |

## 1. Build (CI)

```sh
git push origin main                     # the workflow builds the pushed main

gh -R Yimunan/open-financial-terminal-windows workflow run build-windows.yml \
  -f version=X.Y.Z
gh -R Yimunan/open-financial-terminal-windows run watch
gh -R Yimunan/open-financial-terminal-windows run download --name OpenFinancialTerminal-Setup-windows-x64
```

The workflow: builds the SPA (`npm ci && npm run build`, tsc fails fast) → freezes the backend with
PyInstaller (`backend/oft-backend.spec`, which loads `rthook_env.py`) → compiles the Inno Setup
installer with `/DAppVersion=X.Y.Z` → **smoke-tests the real frozen exe** (`--server-only`, reads
`OFT_URL=` from `%LOCALAPPDATA%\OpenFinancialTerminal\logs\desktop.log`, polls `/api/health` until
`status: ok`) → uploads `OpenFinancialTerminal-Setup.exe` + `SHA256SUMS.txt` as the artifact.
Optionally pass `-f create_release=true` to let CI create/update the release itself.

**Local alternative** (Windows 10/11 x64, Python 3.11+, Node 18+, Inno Setup 6):

```powershell
cd open-financial-terminal
pwsh scripts\setup.ps1                                   # first time only
pwsh scripts\build_desktop.ps1 -Installer -Version X.Y.Z
# → packaging\dist_installer\OpenFinancialTerminal-Setup-X.Y.Z.exe
```

## 2. Verify before shipping

What CI already proved (check the run logs): frontend typechecks + builds, freeze completed, frozen
backend booted and `/api/health` returned `status: ok`, artifact SHA256 recorded.

On any real Windows machine (recommended when available — CI cannot exercise the GUI):

- [ ] installer runs (SmartScreen → More info → Run anyway), both per-user and all-users modes
- [ ] app opens its WebView2 window; chart renders; Ctrl+K opens
- [ ] `curl http://127.0.0.1:8050/api/health` → `"status":"ok"`
- [ ] **filings live** (regression guard for the SEC UA bug — the reason `rthook_env.py` must be in
      the freeze): `curl "http://127.0.0.1:8050/api/filings?symbol=TSLA"` → `"coverage":"live"`
- [ ] macro refresh honest: `curl -X POST http://127.0.0.1:8050/api/settings/data-refresh/macro/run`
      → `"status":"ok"` with `series > 0`
- [ ] LLM round-trip if configured: `curl -X POST http://127.0.0.1:8050/api/settings/llm/probe -d '{}'`

## 3. Cut the release

The installer (~200–250 MB) exceeds GitHub's 100 MB file limit — it ships as a **release asset**,
never in git (`.gitignore` excludes `*.exe`). Keep the asset name **constant**
(`OpenFinancialTerminal-Setup.exe`) so the README's
`releases/latest/download/OpenFinancialTerminal-Setup.exe` button always resolves; the version lives
in the tag and in Add/Remove Programs (via `/DAppVersion`).

```sh
mv OpenFinancialTerminal-Setup-X.Y.Z.exe OpenFinancialTerminal-Setup.exe   # if built locally

gh -R Yimunan/open-financial-terminal-windows release create vX.Y.Z \
  OpenFinancialTerminal-Setup.exe SHA256SUMS.txt \
  --target main \
  --title "Open Financial Terminal vX.Y.Z (Windows x64)" \
  --notes "…what changed + the standard install/SmartScreen blurb (copy a previous release)…"
```

Version in lock-step with the macOS repo when the source state matches (both platforms' v1.0.3 are
the same code). Verify after upload:

```sh
gh -R Yimunan/open-financial-terminal-windows release view --json tagName --jq .tagName   # → vX.Y.Z (latest)
gh -R Yimunan/open-financial-terminal-windows release view vX.Y.Z \
  --json assets --jq '.assets[]|"\(.name) \(.size) \(.state)"'                            # → uploaded
```

## Gotchas learned the hard way

- **`backend/oft-backend.spec` must list `runtime_hooks=["rthook_env.py"]`** — without it the frozen
  build has no `SEC_USER_AGENT` (EDGAR 403s every filings fetch; dev runs mask it) and no seeded
  per-machine `OFT_SECRET_KEY`. The workflow guards this and hard-fails if the hook is missing.
- The spec bundles `frontend/dist` **as it exists on disk** — a stale dist ships stale UI with no
  error. CI always rebuilds; locally, rebuild the SPA when in doubt.
- Keep the asset name constant and the Inno `AppId` constant across releases — the former keeps
  `releases/latest` links stable, the latter makes upgrades replace in place.
- Unsigned → SmartScreen on first run (INSTALL-Windows.md). Real signing: get a cert, then
  `build_desktop.ps1 -Installer -Sign` with `OFT_SIGN_THUMBPRINT` or `OFT_SIGN_PFX`/`_PASSWORD`.
- `#define AppVersion` in `oft-installer.iss` is `#ifndef`-guarded — CI passes `/DAppVersion=`;
  bumping only the tag without `-f version=` ships an installer whose internal version lags.
- The `--server-only` smoke test writes its output to
  `%LOCALAPPDATA%\OpenFinancialTerminal\logs\desktop.log` (the release exe is windowed — stdout is
  redirected), so that log, not the console, is where `OFT_URL=` and tracebacks appear.

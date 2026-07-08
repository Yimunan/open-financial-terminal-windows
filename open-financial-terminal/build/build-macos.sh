#!/usr/bin/env bash
# Build Open Financial Terminal into a native macOS .app + DMG (Apple Silicon).
#
# Prereqs: macOS 12+ arm64, `uv` (brew install uv), Node 18+.
# IMPORTANT: the qhfi checkout MUST include src/qhfi/data and src/qhfi/models — the public repo
# excludes them via a .gitignore bug (see build/README.md). The pre-built DMG already has them.
set -euo pipefail

ROOT="${1:-$HOME/oft-build}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$ROOT"; cd "$ROOT"

echo "==> [1/6] fetch sources (as siblings)"
[ -d open-financial-terminal ]     || git clone https://github.com/Yimunan/open-financial-terminal.git
[ -d quant-hedge-fund-incubator ]  || git clone https://github.com/Yimunan/quant-hedge-fund-incubator.git

if [ ! -f quant-hedge-fund-incubator/src/qhfi/data/base.py ]; then
  echo "!! quant-hedge-fund-incubator/src/qhfi/data is missing (the .gitignore bug)."
  echo "!! Restore src/qhfi/data + src/qhfi/models before building (see build/README.md)."
  exit 1
fi

echo "==> [2/6] copy macOS build tooling into the backend"
cp "$HERE/oft-macos.spec" open-financial-terminal/backend/oft-macos.spec
cp "$HERE/rthook_env.py"  open-financial-terminal/backend/rthook_env.py
cp "$HERE/oft.icns"       open-financial-terminal/packaging/oft.icns

echo "==> [3/6] build the frontend SPA"
( cd open-financial-terminal/frontend && npm ci && npm run build )

echo "==> [4/6] create venv + install (uv, Python 3.11)"
uv venv --python 3.11 .venv-desktop
uv pip install --python .venv-desktop/bin/python \
  -e ./quant-hedge-fund-incubator \
  -e "./open-financial-terminal/backend[desktop]" \
  "pywebview[cocoa]" pyinstaller

echo "==> [5/6] freeze the .app"
( cd open-financial-terminal/backend && \
  "$ROOT/.venv-desktop/bin/pyinstaller" oft-macos.spec --noconfirm \
    --distpath dist-mac --workpath build-mac )
APP="open-financial-terminal/backend/dist-mac/Open Financial Terminal.app"
codesign --force --deep --sign - "$APP"

echo "==> [6/6] package the DMG"
STAGE="$(mktemp -d)/OFT"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"; ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Open Financial Terminal" -srcfolder "$STAGE" \
  -ov -format UDZO "$ROOT/OpenFinancialTerminal.dmg"

echo "Done: $ROOT/OpenFinancialTerminal.dmg"

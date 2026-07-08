"""Regenerate docs/DATA.md + config/data_state.yaml from taxonomy + live lake.

Thin wrapper around `qhfi.data.catalog.refresh()` (the same function every pull script calls at the
end of main()). Run manually any time:  .venv\\Scripts\\python.exe scripts\\gen_data_docs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qhfi.data.catalog import refresh

if __name__ == "__main__":
    md, cfg = refresh()
    print(f"wrote {md}\nwrote {cfg}")

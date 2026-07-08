# Open Financial Terminal — backend

FastAPI app that imports the **qhfi** engine as a library and exposes it over REST + a
streaming chat WebSocket. See the top-level [`README.md`](../README.md) and
[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

## Run

```bash
python -m venv .venv
.venv/Scripts/pip install -e ../../quant-hedge-fund-incubator   # qhfi + heavy deps
.venv/Scripts/pip install -e .                                  # fastapi, uvicorn
.venv/Scripts/uvicorn app.main:app --reload --port 8050
```

Interactive docs: <http://localhost:8050/docs>. Health: <http://localhost:8050/api/health>.

## Layout

- `app/config.py` — `TerminalSettings` (OFT_*) + reused qhfi `Settings`.
- `app/deps.py` — shared singletons (DataManager, LLMClient, SQLite store) + model resolver.
- `app/services/` — adapters from qhfi types to JSON DTOs.
- `app/routers/` — one module per function area.

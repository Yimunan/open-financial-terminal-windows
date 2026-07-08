"""Terminal-owned persistence (SQLite): watchlists, holdings, alerts, workspaces.

This is state the terminal owns and qhfi knows nothing about. Kept deliberately small and
dependency-free (stdlib sqlite3) so the open-source quickstart has no extra moving parts.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock


class TerminalStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._lock = Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def init(self, paper_initial_cash: float = 100_000.0) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                    symbol TEXT PRIMARY KEY,
                    asset  TEXT NOT NULL DEFAULT 'equity',
                    added  TEXT
                );
                -- Multi-book holdings: one named portfolio book per row; `holdings` carries a
                -- `book_id` (composite PK with `symbol`). Legacy single-book DBs are migrated to
                -- book 1 ('Default') in `_migrate_portfolio_books`.
                CREATE TABLE IF NOT EXISTS portfolio_books (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    name    TEXT NOT NULL,
                    created TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS holdings (
                    book_id   INTEGER NOT NULL DEFAULT 1,
                    symbol    TEXT NOT NULL,
                    asset     TEXT NOT NULL DEFAULT 'equity',
                    quantity  REAL NOT NULL DEFAULT 0,
                    cost_basis REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (book_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    spec  TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspaces (
                    name    TEXT PRIMARY KEY,
                    layout  TEXT NOT NULL,
                    updated TEXT
                );
                CREATE TABLE IF NOT EXISTS workspace_templates (
                    name    TEXT PRIMARY KEY,
                    layout  TEXT NOT NULL,
                    updated TEXT
                );
                CREATE TABLE IF NOT EXISTS agent_graphs (
                    name    TEXT PRIMARY KEY,
                    spec    TEXT NOT NULL,
                    updated TEXT
                );
                CREATE TABLE IF NOT EXISTS agent_scenarios (
                    name    TEXT PRIMARY KEY,
                    data    TEXT NOT NULL,
                    updated TEXT
                );
                CREATE TABLE IF NOT EXISTS factor_monitors (
                    name    TEXT PRIMARY KEY,
                    data    TEXT NOT NULL,
                    updated TEXT
                );
                CREATE TABLE IF NOT EXISTS factor_monitor_snapshots (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    monitor TEXT NOT NULL,
                    ts      TEXT NOT NULL,
                    data    TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_account (
                    id             INTEGER PRIMARY KEY CHECK (id = 1),
                    cash           REAL NOT NULL,
                    realized_total REAL NOT NULL DEFAULT 0
                );
                -- Multi-book sim: one row per paper account; the singleton `paper_account` above is
                -- now migration-source only (legacy DBs) — live cash/realized lives here per account.
                CREATE TABLE IF NOT EXISTS paper_accounts (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    name           TEXT NOT NULL,
                    cash           REAL NOT NULL,
                    realized_total REAL NOT NULL DEFAULT 0,
                    initial_cash   REAL NOT NULL,
                    commission_bps REAL NOT NULL DEFAULT 0,
                    slippage_bps   REAL NOT NULL DEFAULT 0,
                    created        TEXT NOT NULL DEFAULT (datetime('now')),
                    archived       INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS paper_positions (
                    account_id INTEGER NOT NULL DEFAULT 1,
                    symbol    TEXT NOT NULL,
                    asset     TEXT NOT NULL DEFAULT 'equity',
                    quantity  REAL NOT NULL DEFAULT 0,
                    avg_price REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (account_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS paper_orders (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id      INTEGER NOT NULL DEFAULT 1,
                    ts              TEXT NOT NULL,
                    symbol          TEXT NOT NULL,
                    asset           TEXT NOT NULL DEFAULT 'equity',
                    side            TEXT NOT NULL,
                    quantity        REAL NOT NULL,
                    type            TEXT NOT NULL DEFAULT 'market',
                    limit_price     REAL,
                    status          TEXT NOT NULL,
                    fill_price      REAL,
                    broker_order_id TEXT,
                    realized_pnl    REAL,
                    stop_price      REAL,
                    trail_pct       REAL,
                    hwm             REAL,
                    filled_ts       TEXT
                );
                CREATE TABLE IF NOT EXISTS paper_equity (
                    account_id INTEGER NOT NULL DEFAULT 1,
                    ts     TEXT NOT NULL,
                    equity REAL NOT NULL,
                    cash   REAL NOT NULL,
                    gross  REAL NOT NULL DEFAULT 0,
                    net    REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS custom_factors (
                    name    TEXT PRIMARY KEY,
                    data    TEXT NOT NULL,
                    updated TEXT
                );
                CREATE TABLE IF NOT EXISTS custom_strategies (
                    name    TEXT PRIMARY KEY,
                    data    TEXT NOT NULL,
                    updated TEXT
                );
                CREATE TABLE IF NOT EXISTS models (
                    name    TEXT PRIMARY KEY,
                    data    TEXT NOT NULL,
                    updated TEXT
                );
                CREATE TABLE IF NOT EXISTS portfolios (
                    name    TEXT PRIMARY KEY,
                    data    TEXT NOT NULL,
                    updated TEXT
                );
                CREATE TABLE IF NOT EXISTS committee_templates (
                    name    TEXT PRIMARY KEY,
                    data    TEXT NOT NULL,
                    updated TEXT
                );
                CREATE TABLE IF NOT EXISTS config (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS algos (
                    id      TEXT PRIMARY KEY,
                    data    TEXT NOT NULL,
                    updated TEXT
                );
                CREATE TABLE IF NOT EXISTS algo_runs (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    algo_id TEXT NOT NULL,
                    ts      TEXT NOT NULL,
                    data    TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_runs (
                    id      TEXT PRIMARY KEY,
                    goal    TEXT NOT NULL,
                    status  TEXT NOT NULL DEFAULT 'running',
                    best    TEXT,
                    created TEXT,
                    updated TEXT
                );
                CREATE TABLE IF NOT EXISTS research_iterations (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id  TEXT NOT NULL,
                    i       INTEGER NOT NULL,
                    ts      TEXT NOT NULL,
                    data    TEXT NOT NULL
                );
                """
            )
            # Additive migrations for DBs created before these columns existed
            # (CREATE TABLE IF NOT EXISTS won't backfill columns). Each ALTER is
            # idempotent via try/except on "duplicate column" for already-migrated DBs.
            for ddl in (
                "ALTER TABLE paper_orders ADD COLUMN realized_pnl REAL",
                "ALTER TABLE paper_account ADD COLUMN realized_total REAL NOT NULL DEFAULT 0",
                "ALTER TABLE paper_orders ADD COLUMN stop_price REAL",
                "ALTER TABLE paper_orders ADD COLUMN trail_pct REAL",
                "ALTER TABLE paper_orders ADD COLUMN hwm REAL",
                # Multi-account: backfill account_id=1 onto pre-account order/equity rows.
                "ALTER TABLE paper_orders ADD COLUMN account_id INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE paper_equity ADD COLUMN account_id INTEGER NOT NULL DEFAULT 1",
                # Metrics layer: exposure history on the equity curve + fill-latency timestamp.
                "ALTER TABLE paper_equity ADD COLUMN gross REAL NOT NULL DEFAULT 0",
                "ALTER TABLE paper_equity ADD COLUMN net REAL NOT NULL DEFAULT 0",
                "ALTER TABLE paper_orders ADD COLUMN filled_ts TEXT",
                # User-defined watchlist ordering (drag-to-reorder); legacy rows backfilled below.
                "ALTER TABLE watchlist ADD COLUMN position INTEGER",
            ):
                try:
                    self._conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass  # column already present
            self._backfill_watchlist_positions()
            self._migrate_paper_accounts(paper_initial_cash)
            self._migrate_portfolio_books()
            self._conn.executescript(
                "CREATE INDEX IF NOT EXISTS idx_paper_orders_account ON paper_orders(account_id);"
                "CREATE INDEX IF NOT EXISTS idx_paper_equity_account ON paper_equity(account_id, ts);"
            )
            self._conn.commit()

    def _migrate_portfolio_books(self) -> None:
        """One-time migration to the multi-book holdings model (idempotent; runs inside init's lock).

        (1) Legacy `holdings` used `symbol` as its sole PK and lacks `book_id` — SQLite can't ALTER a
        PK, so rebuild with the composite (book_id, symbol) PK, re-homing every existing row under
        book 1. (2) Ensure a 'Default' book row (id=1) exists so an upgrading user's single book
        carries over untouched; fresh DBs also get the Default book here."""
        cols = [r["name"] for r in self._conn.execute("PRAGMA table_info(holdings)").fetchall()]
        if "book_id" not in cols:  # pre-book schema → rebuild with the composite PK
            self._conn.executescript(
                "ALTER TABLE holdings RENAME TO holdings_legacy;"
                "CREATE TABLE holdings ("
                "  book_id INTEGER NOT NULL DEFAULT 1, symbol TEXT NOT NULL,"
                "  asset TEXT NOT NULL DEFAULT 'equity', quantity REAL NOT NULL DEFAULT 0,"
                "  cost_basis REAL NOT NULL DEFAULT 0, PRIMARY KEY (book_id, symbol));"
                "INSERT INTO holdings(book_id, symbol, asset, quantity, cost_basis) "
                "  SELECT 1, symbol, asset, quantity, cost_basis FROM holdings_legacy;"
                "DROP TABLE holdings_legacy;"
            )
        if self._conn.execute("SELECT 1 FROM portfolio_books WHERE id = 1").fetchone() is None:
            self._conn.execute("INSERT INTO portfolio_books(id, name) VALUES (1, 'Default')")

    def _backfill_watchlist_positions(self) -> None:
        """Assign a `position` to any watchlist row missing one (legacy rows), appending them after
        the current max in the prior default order (alphabetical by symbol). Idempotent — once every
        row has a position this is a no-op. Runs inside init's lock."""
        m = self._conn.execute("SELECT COALESCE(MAX(position), -1) AS m FROM watchlist").fetchone()["m"]
        nulls = self._conn.execute(
            "SELECT symbol FROM watchlist WHERE position IS NULL ORDER BY symbol"
        ).fetchall()
        for i, r in enumerate(nulls):
            self._conn.execute(
                "UPDATE watchlist SET position = ? WHERE symbol = ?", (int(m) + 1 + i, r["symbol"])
            )

    def _migrate_paper_accounts(self, paper_initial_cash: float) -> None:
        """One-time migration to the multi-account sim model (idempotent; runs inside init's lock).

        Two legacy fixups: (1) `paper_positions` used `symbol` as its sole PK and lacks `account_id`
        — SQLite can't ALTER a PK, so rebuild the table with a composite (account_id, symbol) PK,
        re-homing every existing row under account 1. (2) Copy the singleton `paper_account` row
        (cash + realized ledger) into a 'Default' `paper_accounts` row id=1 so an upgrading user's
        book carries over untouched. Fresh DBs hit neither branch — the Default account is created
        lazily at the broker's initial_cash via ``ensure_paper_account``."""
        pos_cols = [r["name"] for r in self._conn.execute("PRAGMA table_info(paper_positions)").fetchall()]
        if "account_id" not in pos_cols:  # pre-account schema → rebuild with the composite PK
            self._conn.executescript(
                "ALTER TABLE paper_positions RENAME TO paper_positions_legacy;"
                "CREATE TABLE paper_positions ("
                "  account_id INTEGER NOT NULL DEFAULT 1, symbol TEXT NOT NULL,"
                "  asset TEXT NOT NULL DEFAULT 'equity', quantity REAL NOT NULL DEFAULT 0,"
                "  avg_price REAL NOT NULL DEFAULT 0, PRIMARY KEY (account_id, symbol));"
                "INSERT INTO paper_positions(account_id, symbol, asset, quantity, avg_price) "
                "  SELECT 1, symbol, asset, quantity, avg_price FROM paper_positions_legacy;"
                "DROP TABLE paper_positions_legacy;"
            )
        has_default = self._conn.execute("SELECT 1 FROM paper_accounts WHERE id = 1").fetchone()
        legacy = self._conn.execute("SELECT cash, realized_total FROM paper_account WHERE id = 1").fetchone()
        if has_default is None and legacy is not None:
            self._conn.execute(
                "INSERT INTO paper_accounts(id, name, cash, realized_total, initial_cash, "
                "commission_bps, slippage_bps) VALUES (1, 'Default', ?, ?, ?, ?, ?)",
                (float(legacy["cash"]), float(legacy["realized_total"]), paper_initial_cash,
                 self._cfg_bps("paper_commission_bps"), self._cfg_bps("paper_slippage_bps")),
            )

    def _cfg_bps(self, key: str) -> float:
        """Read a clamped commission/slippage bps from the config table (0 if unset/invalid)."""
        row = self._conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        try:
            return max(0.0, min(500.0, float(row["value"]))) if row else 0.0
        except (TypeError, ValueError):
            return 0.0

    # ── config (simple key/value, e.g. registry directory paths) ───────────────
    def get_config(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_config(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO config(key, value) VALUES (?, ?)", (key, value))
            self._conn.commit()

    # ── algo trading (runner configs + per-cycle run log) ──────────────────────
    # An algo is keyed by its own id (not a name) so several can target the same symbol.
    # The full config blob lives in `data`; `algo_runs` is an append-only cycle log.
    def list_algos(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, data, updated FROM algos ORDER BY updated DESC, id"
            ).fetchall()
        out = []
        for r in rows:
            rec = json.loads(r["data"])
            rec["id"] = r["id"]
            rec["updated"] = r["updated"]
            out.append(rec)
        return out

    def get_algo(self, algo_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, data, updated FROM algos WHERE id = ?", (algo_id,)
            ).fetchone()
        if row is None:
            return None
        rec = json.loads(row["data"])
        rec["id"] = row["id"]
        rec["updated"] = row["updated"]
        return rec

    def save_algo(self, algo_id: str, record: dict) -> None:
        payload = {k: v for k, v in record.items() if k not in ("id", "updated")}
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO algos(id, data, updated) VALUES (?, ?, datetime('now'))",
                (algo_id, json.dumps(payload)),
            )
            self._conn.commit()

    def remove_algo(self, algo_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM algos WHERE id = ?", (algo_id,))
            self._conn.execute("DELETE FROM algo_runs WHERE algo_id = ?", (algo_id,))
            self._conn.commit()

    def add_algo_run(self, algo_id: str, data: dict) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO algo_runs(algo_id, ts, data) VALUES (?, datetime('now'), ?)",
                (algo_id, json.dumps(data)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_algo_runs(self, algo_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, data FROM algo_runs WHERE algo_id = ? ORDER BY id DESC LIMIT ?",
                (algo_id, limit),
            ).fetchall()
        return [{"id": r["id"], "ts": r["ts"], **json.loads(r["data"])} for r in rows]

    # ── research loop (autonomous design→generate→evaluate→reflect runs) ────────
    # A run is the parent (goal + status + best-so-far); `research_iterations` is the
    # append-only per-iteration log (carries each iteration's full dashboard payload),
    # mirroring the algos / algo_runs parent+child pattern above.
    def create_research_run(self, goal: str) -> str:
        import uuid

        run_id = f"r{uuid.uuid4().hex[:10]}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO research_runs(id, goal, status, created, updated) "
                "VALUES (?, ?, 'running', datetime('now'), datetime('now'))",
                (run_id, goal),
            )
            self._conn.commit()
        return run_id

    def add_research_iteration(self, run_id: str, i: int, data: dict) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO research_iterations(run_id, i, ts, data) VALUES (?, ?, datetime('now'), ?)",
                (run_id, int(i), json.dumps(data)),
            )
            self._conn.execute(
                "UPDATE research_runs SET updated = datetime('now') WHERE id = ?", (run_id,)
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def finalize_research_run(self, run_id: str, best: dict, status: str = "done") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE research_runs SET best = ?, status = ?, updated = datetime('now') WHERE id = ?",
                (json.dumps(best), status, run_id),
            )
            self._conn.commit()

    def list_research_runs(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, goal, status, best, created, updated FROM research_runs "
                "ORDER BY created DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r["id"], "goal": r["goal"], "status": r["status"],
                "best": json.loads(r["best"]) if r["best"] else None,
                "created": r["created"], "updated": r["updated"],
            }
            for r in rows
        ]

    def get_research_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, goal, status, best, created, updated FROM research_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"], "goal": row["goal"], "status": row["status"],
            "best": json.loads(row["best"]) if row["best"] else None,
            "created": row["created"], "updated": row["updated"],
        }

    def list_research_iterations(self, run_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT i, ts, data FROM research_iterations WHERE run_id = ? ORDER BY i ASC",
                (run_id,),
            ).fetchall()
        return [{"ts": r["ts"], **json.loads(r["data"])} for r in rows]

    def remove_research_run(self, run_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM research_runs WHERE id = ?", (run_id,))
            self._conn.execute("DELETE FROM research_iterations WHERE run_id = ?", (run_id,))
            self._conn.commit()

    # ── registries (custom factors / strategies / research models) ─────────────
    # Each row stores the full record as JSON in `data`; thin generic CRUD below.
    def _reg_list(self, table: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(f"SELECT data, updated FROM {table} ORDER BY name").fetchall()
        out = []
        for r in rows:
            rec = json.loads(r["data"])
            rec["updated"] = r["updated"]
            out.append(rec)
        return out

    def _reg_get(self, table: str, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(f"SELECT data, updated FROM {table} WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        rec = json.loads(row["data"])
        rec["updated"] = row["updated"]
        return rec

    def _reg_save(self, table: str, name: str, record: dict) -> None:
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO {table}(name, data, updated) VALUES (?, ?, datetime('now'))",
                (name, json.dumps(record)),
            )
            self._conn.commit()

    def _reg_remove(self, table: str, name: str) -> None:
        with self._lock:
            self._conn.execute(f"DELETE FROM {table} WHERE name = ?", (name,))
            self._conn.commit()

    def list_custom_factors(self) -> list[dict]:
        return self._reg_list("custom_factors")

    def save_custom_factor(self, name: str, record: dict) -> None:
        self._reg_save("custom_factors", name, record)

    def remove_custom_factor(self, name: str) -> None:
        self._reg_remove("custom_factors", name)

    def list_custom_strategies(self) -> list[dict]:
        return self._reg_list("custom_strategies")

    def get_custom_strategy(self, name: str) -> dict | None:
        return self._reg_get("custom_strategies", name)

    def save_custom_strategy(self, name: str, record: dict) -> None:
        self._reg_save("custom_strategies", name, record)

    def remove_custom_strategy(self, name: str) -> None:
        self._reg_remove("custom_strategies", name)

    def list_models(self) -> list[dict]:
        return self._reg_list("models")

    def save_model(self, name: str, record: dict) -> None:
        self._reg_save("models", name, record)

    def remove_model(self, name: str) -> None:
        self._reg_remove("models", name)

    def list_portfolios(self) -> list[dict]:
        return self._reg_list("portfolios")

    def save_portfolio(self, name: str, record: dict) -> None:
        self._reg_save("portfolios", name, record)

    def remove_portfolio(self, name: str) -> None:
        self._reg_remove("portfolios", name)

    # ── committee templates (reusable Investment Committee rosters) ─────────────
    def list_committee_templates(self) -> list[dict]:
        return self._reg_list("committee_templates")

    def get_committee_template(self, name: str) -> dict | None:
        return self._reg_get("committee_templates", name)

    def save_committee_template(self, name: str, record: dict) -> None:
        self._reg_save("committee_templates", name, record)

    def remove_committee_template(self, name: str) -> None:
        self._reg_remove("committee_templates", name)

    def list_scenarios(self) -> list[dict]:
        return self._reg_list("agent_scenarios")

    def save_scenario(self, name: str, record: dict) -> None:
        self._reg_save("agent_scenarios", name, record)

    def remove_scenario(self, name: str) -> None:
        self._reg_remove("agent_scenarios", name)

    def list_factor_monitors(self) -> list[dict]:
        return self._reg_list("factor_monitors")

    def save_factor_monitor(self, name: str, record: dict) -> None:
        self._reg_save("factor_monitors", name, record)

    def remove_factor_monitor(self, name: str) -> None:
        self._reg_remove("factor_monitors", name)

    def add_monitor_snapshot(self, monitor: str, data: dict) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO factor_monitor_snapshots(monitor, ts, data) VALUES (?, datetime('now'), ?)",
                (monitor, json.dumps(data)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_monitor_snapshots(self, monitor: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, data FROM factor_monitor_snapshots WHERE monitor = ? ORDER BY id DESC LIMIT ?",
                (monitor, limit),
            ).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "data": json.loads(r["data"])} for r in rows]

    # ── watchlist (user-ordered via `position`; drag-to-reorder) ─────────────────
    def list_watchlist(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT symbol, asset FROM watchlist ORDER BY position, symbol"
            ).fetchall()
        return [dict(r) for r in rows]

    def add_watch(self, symbol: str, asset: str = "equity") -> None:
        """Add a symbol at the end of the list (or update its asset if it already exists). A re-add
        keeps the symbol's existing position rather than jumping it to the bottom."""
        sym = symbol.upper()
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM watchlist WHERE symbol = ?", (sym,)
            ).fetchone()
            if exists is None:
                nxt = self._conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 AS n FROM watchlist"
                ).fetchone()["n"]
                self._conn.execute(
                    "INSERT INTO watchlist(symbol, asset, added, position) "
                    "VALUES (?, ?, datetime('now'), ?)",
                    (sym, asset, nxt),
                )
            else:
                self._conn.execute("UPDATE watchlist SET asset = ? WHERE symbol = ?", (asset, sym))
            self._conn.commit()

    def remove_watch(self, symbol: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
            self._conn.commit()

    def reorder_watchlist(self, symbols: list[str]) -> None:
        """Persist a new ordering. `symbols` is the full list in the desired order; any symbol not
        named keeps its old position (sorted after the reordered ones on next list)."""
        with self._lock:
            for i, s in enumerate(symbols):
                self._conn.execute(
                    "UPDATE watchlist SET position = ? WHERE symbol = ?", (i, s.upper())
                )
            self._conn.commit()

    # ── portfolio books (named multi-book holdings) ────────────────────────────
    # A *book* is a named holdings list. Server-side consumers (risk, assistant, composition,
    # data-refresh) read the *active* book — its id is persisted in config under
    # ``active_portfolio_book`` — so switching books in the UI re-points them automatically.
    def list_portfolio_books(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, created FROM portfolio_books ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def active_portfolio_book(self) -> int:
        """The currently-selected book id (defaults to 1; falls back to the lowest existing book if
        the stored id was deleted out from under us)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM config WHERE key = 'active_portfolio_book'"
            ).fetchone()
            try:
                bid = int(row["value"]) if row else 1
            except (TypeError, ValueError):
                bid = 1
            exists = self._conn.execute(
                "SELECT 1 FROM portfolio_books WHERE id = ?", (bid,)
            ).fetchone()
            if exists is None:
                fallback = self._conn.execute(
                    "SELECT id FROM portfolio_books ORDER BY id LIMIT 1"
                ).fetchone()
                bid = int(fallback["id"]) if fallback else 1
        return bid

    def set_active_portfolio_book(self, book_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO config(key, value) VALUES ('active_portfolio_book', ?)",
                (str(int(book_id)),),
            )
            self._conn.commit()

    def create_portfolio_book(self, name: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO portfolio_books(name) VALUES (?)", (name.strip() or "Untitled",)
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def rename_portfolio_book(self, book_id: int, name: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE portfolio_books SET name = ? WHERE id = ?", (name.strip() or "Untitled", book_id)
            )
            self._conn.commit()

    def delete_portfolio_book(self, book_id: int) -> None:
        """Hard-delete a book and its holdings. Refuses to delete the last remaining book; if the
        deleted book was active, the active pointer falls back lazily via ``active_portfolio_book``."""
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) AS n FROM portfolio_books").fetchone()["n"]
            if int(n) <= 1:
                raise ValueError("cannot delete the last portfolio")
            self._conn.execute("DELETE FROM holdings WHERE book_id = ?", (book_id,))
            self._conn.execute("DELETE FROM portfolio_books WHERE id = ?", (book_id,))
            self._conn.commit()

    # ── holdings (scoped to a book; `book_id=None` → the active book) ───────────
    def _book(self, book_id: int | None) -> int:
        return self.active_portfolio_book() if book_id is None else int(book_id)

    def list_holdings(self, book_id: int | None = None) -> list[dict]:
        bid = self._book(book_id)
        with self._lock:
            rows = self._conn.execute(
                "SELECT symbol, asset, quantity, cost_basis FROM holdings WHERE book_id = ? ORDER BY symbol",
                (bid,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_holding(
        self, symbol: str, asset: str, quantity: float, cost_basis: float, book_id: int | None = None
    ) -> None:
        bid = self._book(book_id)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO holdings(book_id, symbol, asset, quantity, cost_basis) "
                "VALUES (?, ?, ?, ?, ?)",
                (bid, symbol.upper(), asset, quantity, cost_basis),
            )
            self._conn.commit()

    def remove_holding(self, symbol: str, book_id: int | None = None) -> None:
        bid = self._book(book_id)
        with self._lock:
            self._conn.execute(
                "DELETE FROM holdings WHERE book_id = ? AND symbol = ?", (bid, symbol.upper())
            )
            self._conn.commit()

    # ── alerts ─────────────────────────────────────────────────────────────────
    def list_alerts(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT id, spec FROM alerts ORDER BY id").fetchall()
        return [{"id": r["id"], **json.loads(r["spec"])} for r in rows]

    def add_alert(self, spec: dict) -> int:
        with self._lock:
            cur = self._conn.execute("INSERT INTO alerts(spec) VALUES (?)", (json.dumps(spec),))
            self._conn.commit()
            return int(cur.lastrowid)

    def remove_alert(self, alert_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
            self._conn.commit()

    # ── workspaces (named Dockview layouts) ────────────────────────────────────
    def list_workspaces(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT name, updated FROM workspaces ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def get_workspace(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT name, layout, updated FROM workspaces WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            return None
        return {"name": row["name"], "layout": json.loads(row["layout"]), "updated": row["updated"]}

    def save_workspace(self, name: str, layout: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO workspaces(name, layout, updated) VALUES (?, ?, datetime('now'))",
                (name, json.dumps(layout)),
            )
            self._conn.commit()

    def remove_workspace(self, name: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM workspaces WHERE name = ?", (name,))
            self._conn.commit()

    # ── workspace templates (reusable layout snapshots) ────────────────────────
    def list_templates(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, updated FROM workspace_templates ORDER BY name"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_template(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT name, layout, updated FROM workspace_templates WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            return None
        return {"name": row["name"], "layout": json.loads(row["layout"]), "updated": row["updated"]}

    def save_template(self, name: str, layout: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO workspace_templates(name, layout, updated) "
                "VALUES (?, ?, datetime('now'))",
                (name, json.dumps(layout)),
            )
            self._conn.commit()

    def remove_template(self, name: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM workspace_templates WHERE name = ?", (name,))
            self._conn.commit()

    # ── agent graphs (saved visual workflow specs) ─────────────────────────────
    def list_agent_graphs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, updated FROM agent_graphs ORDER BY name"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_agent_graph(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT name, spec, updated FROM agent_graphs WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            return None
        return {"name": row["name"], "spec": json.loads(row["spec"]), "updated": row["updated"]}

    def save_agent_graph(self, name: str, spec: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO agent_graphs(name, spec, updated) VALUES (?, ?, datetime('now'))",
                (name, json.dumps(spec)),
            )
            self._conn.commit()

    def remove_agent_graph(self, name: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM agent_graphs WHERE name = ?", (name,))
            self._conn.commit()

    # ── paper accounts (multi-book sim) ─────────────────────────────────────────
    # Each row is an independent sim book (its own cash, realized ledger, positions, orders, equity
    # curve). Account 1 is the default; the broker layer addresses an account via the `sim:<id>`
    # book token. Every paper_* method below is scoped by `account_id` (default 1 keeps the legacy
    # single-book callers working).
    _ACCOUNT_COLS = (
        "id, name, cash, realized_total, initial_cash, commission_bps, slippage_bps, created, archived"
    )

    def list_paper_accounts(self, include_archived: bool = False) -> list[dict]:
        q = f"SELECT {self._ACCOUNT_COLS} FROM paper_accounts"
        if not include_archived:
            q += " WHERE archived = 0"
        q += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(q).fetchall()
        return [dict(r) for r in rows]

    def get_paper_account(self, account_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {self._ACCOUNT_COLS} FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()
        return dict(row) if row else None

    def count_paper_accounts(self, include_archived: bool = False) -> int:
        q = "SELECT COUNT(*) AS n FROM paper_accounts"
        if not include_archived:
            q += " WHERE archived = 0"
        with self._lock:
            return int(self._conn.execute(q).fetchone()["n"])

    def ensure_paper_account(
        self, account_id: int, name: str, initial_cash: float,
        commission_bps: float = 0.0, slippage_bps: float = 0.0,
    ) -> None:
        """Create the account row at `initial_cash` only if it doesn't yet exist (INSERT OR IGNORE).

        Never overwrites a live row, so it's safe to call on every broker construction — a fresh
        book is seeded once; an existing book's cash/realized survive broker rebuilds (e.g. after a
        realism-config change clears the broker cache)."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO paper_accounts(id, name, cash, realized_total, initial_cash, "
                "commission_bps, slippage_bps) VALUES (?, ?, ?, 0, ?, ?, ?)",
                (account_id, name, initial_cash, initial_cash, commission_bps, slippage_bps),
            )
            self._conn.commit()

    def create_paper_account(
        self, name: str, initial_cash: float,
        commission_bps: float = 0.0, slippage_bps: float = 0.0,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO paper_accounts(name, cash, realized_total, initial_cash, "
                "commission_bps, slippage_bps) VALUES (?, ?, 0, ?, ?, ?)",
                (name, initial_cash, initial_cash, commission_bps, slippage_bps),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def rename_paper_account(self, account_id: int, name: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE paper_accounts SET name = ? WHERE id = ?", (name, account_id))
            self._conn.commit()

    def update_paper_account_config(
        self, account_id: int, *, initial_cash: float | None = None,
        commission_bps: float | None = None, slippage_bps: float | None = None,
    ) -> None:
        """Patch an account's config. `initial_cash` is the reset baseline only — it does NOT touch
        the live `cash` balance (use reset_paper for that)."""
        sets, params = [], []
        if initial_cash is not None:
            sets.append("initial_cash = ?"); params.append(initial_cash)
        if commission_bps is not None:
            sets.append("commission_bps = ?"); params.append(commission_bps)
        if slippage_bps is not None:
            sets.append("slippage_bps = ?"); params.append(slippage_bps)
        if not sets:
            return
        params.append(account_id)
        with self._lock:
            self._conn.execute(f"UPDATE paper_accounts SET {', '.join(sets)} WHERE id = ?", params)
            self._conn.commit()

    def archive_paper_account(self, account_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE paper_accounts SET archived = 1 WHERE id = ?", (account_id,))
            self._conn.commit()

    def delete_paper_account(self, account_id: int) -> None:
        """Hard-delete an account and cascade its book (positions/orders/equity)."""
        with self._lock:
            self._conn.execute("DELETE FROM paper_positions WHERE account_id = ?", (account_id,))
            self._conn.execute("DELETE FROM paper_orders WHERE account_id = ?", (account_id,))
            self._conn.execute("DELETE FROM paper_equity WHERE account_id = ?", (account_id,))
            self._conn.execute("DELETE FROM paper_accounts WHERE id = ?", (account_id,))
            self._conn.commit()

    # ── paper trading (per-account SimBroker state) ─────────────────────────────
    def paper_cash(self, account_id: int = 1) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT cash FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()
        return float(row["cash"]) if row else 0.0

    def set_paper_cash(self, cash: float, account_id: int = 1) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE paper_accounts SET cash = ? WHERE id = ?", (cash, account_id)
            )
            self._conn.commit()

    # ── realized P&L (running total + per-close ledger on the order row) ─────────
    def paper_realized_total(self, account_id: int = 1) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT realized_total FROM paper_accounts WHERE id = ?", (account_id,)
            ).fetchone()
        return float(row["realized_total"]) if row else 0.0

    def add_paper_realized(self, order_id: int, amount: float, account_id: int = 1) -> None:
        """Stamp realized P&L on the closing order and bump the account's running total."""
        with self._lock:
            self._conn.execute(
                "UPDATE paper_orders SET realized_pnl = ? WHERE id = ?", (amount, order_id)
            )
            self._conn.execute(
                "UPDATE paper_accounts SET realized_total = realized_total + ? WHERE id = ?",
                (amount, account_id),
            )
            self._conn.commit()

    # ── equity curve snapshots (throttled per account) ──────────────────────────
    def add_equity_snapshot(
        self, equity: float, cash: float, min_interval_s: int = 60, account_id: int = 1,
        gross: float = 0.0, net: float = 0.0,
    ) -> None:
        """Append an equity/cash/exposure snapshot for one account, skipping if its last one is
        < min_interval_s old. ``gross``/``net`` carry the book's exposure so /performance can serve
        an exposure time series without a second store."""
        with self._lock:
            row = self._conn.execute(
                "SELECT ts FROM paper_equity WHERE account_id = ? ORDER BY ts DESC LIMIT 1",
                (account_id,),
            ).fetchone()
            if row is not None:
                recent = self._conn.execute(
                    "SELECT 1 WHERE (julianday('now') - julianday(?)) * 86400.0 < ?",
                    (row["ts"], min_interval_s),
                ).fetchone()
                if recent is not None:
                    return
            self._conn.execute(
                "INSERT INTO paper_equity(account_id, ts, equity, cash, gross, net) "
                "VALUES (?, datetime('now'), ?, ?, ?, ?)",
                (account_id, equity, cash, gross, net),
            )
            self._conn.commit()

    def paper_equity_curve(self, limit: int = 500, account_id: int = 1) -> list[dict]:
        # Order by rowid (insertion order) not ts: it's chronological AND a stable tiebreak when
        # several snapshots land in the same second (ts has 1s resolution).
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, equity, cash, gross, net FROM (SELECT rowid AS rid, ts, equity, cash, "
                "gross, net FROM paper_equity WHERE account_id = ? ORDER BY rowid DESC LIMIT ?) "
                "ORDER BY rid ASC",
                (account_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def paper_positions(self, account_id: int = 1) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT symbol, asset, quantity, avg_price FROM paper_positions "
                "WHERE account_id = ? ORDER BY symbol",
                (account_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_paper_position(
        self, symbol: str, asset: str, quantity: float, avg_price: float, account_id: int = 1
    ) -> None:
        with self._lock:
            if abs(quantity) < 1e-9:
                self._conn.execute(
                    "DELETE FROM paper_positions WHERE account_id = ? AND symbol = ?",
                    (account_id, symbol),
                )
            else:
                self._conn.execute(
                    "INSERT OR REPLACE INTO paper_positions(account_id, symbol, asset, quantity, avg_price) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (account_id, symbol, asset, quantity, avg_price),
                )
            self._conn.commit()

    def add_paper_order(self, order: dict, account_id: int = 1) -> int:
        # A row inserted already-filled (market fill at submit) gets filled_ts = ts → ~0 latency;
        # resting orders insert with filled_ts NULL and stamp it later in update_paper_order.
        filled_now = order["status"] == "filled"
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO paper_orders(account_id, ts, symbol, asset, side, quantity, type, limit_price, "
                "status, fill_price, broker_order_id, stop_price, trail_pct, hwm, filled_ts) "
                "VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                + ("datetime('now')" if filled_now else "NULL") + ")",
                (
                    account_id,
                    order["symbol"], order["asset"], order["side"], order["quantity"],
                    order["type"], order.get("limit_price"), order["status"],
                    order.get("fill_price"), order.get("broker_order_id"),
                    order.get("stop_price"), order.get("trail_pct"), order.get("hwm"),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update_paper_order(self, order_id: int, status: str, fill_price: float | None) -> None:
        # Stamp filled_ts the moment a resting order fills (only if not already set), so fill latency
        # = filled_ts - ts is measurable; non-fill transitions (e.g. cancel) leave it NULL.
        with self._lock:
            if status == "filled":
                self._conn.execute(
                    "UPDATE paper_orders SET status = ?, fill_price = ?, "
                    "filled_ts = COALESCE(filled_ts, datetime('now')) WHERE id = ?",
                    (status, fill_price, order_id),
                )
            else:
                self._conn.execute(
                    "UPDATE paper_orders SET status = ?, fill_price = ? WHERE id = ?",
                    (status, fill_price, order_id),
                )
            self._conn.commit()

    def update_paper_order_trail(self, order_id: int, stop_price: float, hwm: float) -> None:
        """Advance a trailing stop's tracked stop_price/high-water-mark as the price moves favorably."""
        with self._lock:
            self._conn.execute(
                "UPDATE paper_orders SET stop_price = ?, hwm = ? WHERE id = ?",
                (stop_price, hwm, order_id),
            )
            self._conn.commit()

    def convert_paper_order_to_limit(self, order_id: int, limit_price: float) -> None:
        """A triggered stop-limit becomes a resting limit: retype the row and clear its stop fields."""
        with self._lock:
            self._conn.execute(
                "UPDATE paper_orders SET type = 'limit', limit_price = ?, stop_price = NULL, "
                "trail_pct = NULL, hwm = NULL WHERE id = ?",
                (limit_price, order_id),
            )
            self._conn.commit()

    _ORDER_COLS = (
        "id, ts, symbol, asset, side, quantity, type, limit_price, status, "
        "fill_price, broker_order_id, realized_pnl, stop_price, trail_pct, hwm, filled_ts"
    )

    def paper_order_status_counts(self, account_id: int = 1) -> dict[str, int]:
        """Count of orders by status (filled/open/cancelled) for one account."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM paper_orders WHERE account_id = ? GROUP BY status",
                (account_id,),
            ).fetchall()
        return {r["status"]: int(r["n"]) for r in rows}

    def paper_fill_latencies(self, account_id: int = 1) -> list[float]:
        """Seconds between submit (ts) and fill (filled_ts) for every filled order with both stamps.

        Market fills stamp filled_ts = ts so they land at ~0; resting orders (limit/stop) carry the
        real wait until they became marketable. Pre-migration rows (filled_ts NULL) are skipped."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT (julianday(filled_ts) - julianday(ts)) * 86400.0 AS secs FROM paper_orders "
                "WHERE account_id = ? AND status = 'filled' AND filled_ts IS NOT NULL",
                (account_id,),
            ).fetchall()
        return [max(0.0, float(r["secs"])) for r in rows if r["secs"] is not None]

    def list_paper_orders(self, limit: int = 50, account_id: int = 1) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {self._ORDER_COLS} FROM paper_orders WHERE account_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def open_paper_orders(self, account_id: int = 1) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {self._ORDER_COLS} FROM paper_orders "
                "WHERE account_id = ? AND status = 'open' ORDER BY id",
                (account_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def reset_paper(self, cash: float, account_id: int = 1) -> None:
        """Wipe one account's book (positions/orders/equity) and reset its cash; keep the account row."""
        with self._lock:
            self._conn.execute("DELETE FROM paper_positions WHERE account_id = ?", (account_id,))
            self._conn.execute("DELETE FROM paper_orders WHERE account_id = ?", (account_id,))
            self._conn.execute("DELETE FROM paper_equity WHERE account_id = ?", (account_id,))
            self._conn.execute(
                "UPDATE paper_accounts SET cash = ?, realized_total = 0 WHERE id = ?",
                (cash, account_id),
            )
            self._conn.commit()

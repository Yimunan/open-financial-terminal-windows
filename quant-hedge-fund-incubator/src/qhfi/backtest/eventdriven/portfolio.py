"""Portfolio — the book and the accounting brain of the event loop.

Holds positions/cash/marks and runs the *same* per-bar accounting as ``backtest.engine`` so the
two engines agree: on each ``MarketEvent`` it settles variation margin, finances the pre-trade
book, and turns the **previous** bar's stored target (the ``signal_lag`` look-ahead guard) into
``OrderEvent``s via the sizing model + no-trade band + lot rounding. ``FillEvent``s update
positions/cash; the end-of-heartbeat ``RecordEvent`` applies carry, re-marks, and records the row.

Signals are buffered by heartbeat: the target executed at bar *t* is the signal received at
*t − signal_lag* (default 1), reproducing the vectorized engine's ``weights.shift(signal_lag)``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.backtest.engine import BacktestResult, ExecutionConfig, _round_to_lot
from qhfi.backtest.eventdriven.events import FillEvent, MarketEvent, OrderEvent
from qhfi.backtest.fills import FillTiming
from qhfi.backtest.financing import FinancingModel
from qhfi.core.types import Instrument, Panel, TargetWeights
from qhfi.portfolio.sizing import CompositeSizing, SizingModel


class Portfolio:
    def __init__(
        self,
        instruments: dict[str, Instrument],
        sizing: SizingModel | None = None,
        financing: FinancingModel | None = None,
        execution: ExecutionConfig | None = None,
        initial_equity: float = 100_000.0,
        carry: Panel | None = None,
        universe_name: str = "",
    ) -> None:
        self.instr = instruments
        self.cols = list(instruments)
        self.sizing = sizing or CompositeSizing()
        self.financing = financing or FinancingModel()
        self.exec = execution or ExecutionConfig()
        self.initial_equity = initial_equity
        self.carry = carry
        self.universe_name = universe_name

        self.positions = {c: 0.0 for c in self.cols}
        self.last_px = {c: np.nan for c in self.cols}
        self.prev_mark = {c: np.nan for c in self.cols}
        self.cash = initial_equity
        self.prev_equity = initial_equity

        # signal-lag buffer: one entry appended per heartbeat (at record time)
        self._signal_history: list[dict[str, float] | None] = []
        self._current_signal: dict[str, float] | None = None
        self._mkt_count = 0

        # per-heartbeat accumulators
        self._equity_pre = initial_equity
        self._day_fin = self._day_comm = self._day_slip = self._day_carry = self._day_traded = 0.0

        self._idx: list[pd.Timestamp] = []
        self._eq, self._cash_l, self._ret = [], [], []
        self._gross, self._net, self._to = [], [], []
        self._comm, self._slip, self._fin, self._carry_l = [], [], [], []
        self._pos_snaps: list[dict[str, float]] = []
        self._wt_snaps: list[dict[str, float]] = []
        self._trades: list[dict] = []

    # ── marks / equity ──────────────────────────────────────────────────────────
    def _notional(self, c: str) -> float:
        lp = self.last_px[c]
        return self.positions[c] * lp * self.instr[c].contract_multiplier if (lp == lp and self.positions[c]) else 0.0

    def _cash_holdings(self) -> float:
        return sum(self._notional(c) for c in self.cols if not self.instr[c].is_margined)

    # ── BookView (read-only, for native strategies) ──────────────────────────────
    def equity(self) -> float:
        return self.cash + self._cash_holdings()

    def position(self, instrument: str) -> float:
        return self.positions.get(instrument, 0.0)

    def last_price(self, instrument: str) -> float:
        return self.last_px.get(instrument, np.nan)

    # ── quoting path (market-maker) ───────────────────────────────────────────────
    # The market-maker bypasses on_market/on_signal (no sizing model, no signal lag, no
    # no-trade band): the matching handler emits fills directly. These three additive helpers
    # let the quoting loop reset per-heartbeat accumulators, re-mark between fills, and book
    # traded notional — without touching the weight-path accounting above.
    def open_heartbeat(self) -> None:
        """Start a new quoting heartbeat: snapshot pre-trade equity and zero the per-bar costs."""
        self._equity_pre = self.cash + self._cash_holdings()
        self._day_fin = self._day_comm = self._day_slip = self._day_carry = self._day_traded = 0.0

    def mark(self, instrument: str, price: float) -> None:
        """Re-mark one instrument to ``price`` (settles VM for margined forms; refreshes last_px)."""
        if price != price:                                  # NaN guard
            return
        c = instrument
        ins = self.instr[c]
        if ins.is_margined and self.positions[c] and self.prev_mark[c] == self.prev_mark[c]:
            self.cash += self.positions[c] * (price - self.prev_mark[c]) * ins.contract_multiplier
        self.prev_mark[c] = price
        self.last_px[c] = price

    def add_traded_notional(self, notional: float) -> None:
        """Accumulate traded notional for this heartbeat's turnover figure (quoting path)."""
        self._day_traded += abs(notional)

    # ── event handlers ────────────────────────────────────────────────────────────
    def on_market(self, event: MarketEvent) -> list[OrderEvent]:
        """Mark + finance, then emit the orders that execute the lagged target this bar."""
        px = event.prices

        # 1. variation margin on margined positions held overnight; refresh marks.
        for c, p in px.items():
            ins = self.instr[c]
            if ins.is_margined and self.positions[c] and self.prev_mark[c] == self.prev_mark[c]:
                self.cash += self.positions[c] * (p - self.prev_mark[c]) * ins.contract_multiplier
            self.prev_mark[c] = p
            self.last_px[c] = p

        # 2. financing on the pre-trade economic book.
        equity_pre = self.cash + self._cash_holdings()
        long_notional = sum(v for c in self.cols if (v := self._notional(c)) > 0)
        short_notional = -sum(v for c in self.cols if (v := self._notional(c)) < 0)
        fin = self.financing.daily_carry(self.cash, equity_pre, long_notional, short_notional)
        self.cash -= fin
        self._equity_pre = equity_pre
        self._day_fin, self._day_comm, self._day_slip, self._day_carry, self._day_traded = fin, 0.0, 0.0, 0.0, 0.0

        # 3. the target due this bar is the signal from `signal_lag` heartbeats ago.
        due = self._mkt_count - self.exec.signal_lag
        target = self._signal_history[due] if 0 <= due < len(self._signal_history) else None
        self._mkt_count += 1

        # 4. size the target into orders (only instruments that printed a bar this timestamp).
        orders: list[OrderEvent] = []
        if target is not None:
            for c, p in px.items():
                ins = self.instr[c]
                w = target.get(c, 0.0)
                w = 0.0 if w != w else float(w)
                if w < 0 and not ins.shortable:
                    w = 0.0
                target_units = self.sizing.target_units(ins, w, equity_pre, p)
                if not self.exec.allow_fractional:
                    target_units = _round_to_lot(target_units, ins.lot_size)
                delta = target_units - self.positions[c]
                if delta == 0:
                    continue
                denom = p * ins.contract_multiplier
                if abs(delta * denom) < self.exec.rebalance_threshold * equity_pre:
                    continue
                ref = p
                if self.exec.fill is FillTiming.NEXT_OPEN and event.opens and c in event.opens:
                    ref = event.opens[c]
                orders.append(OrderEvent(timestamp=event.timestamp, instrument=c,
                                         delta_units=delta, ref_price=ref))
                self._day_traded += abs(delta) * ref * ins.contract_multiplier
        return orders

    def on_signal(self, event) -> None:
        self._current_signal = {**(self._current_signal or {}), **event.targets}

    def apply_fill(self, fill: FillEvent) -> None:
        c = fill.instrument
        mult = self.instr[c].contract_multiplier
        if fill.margined:
            # no notional debit; realize same-day VM (close vs fill) + commission.
            self.cash += fill.delta_units * (self.last_px[c] - fill.fill_price) * mult
            self.cash -= fill.commission
        else:
            self.cash -= fill.delta_units * fill.fill_price * mult
            self.cash -= fill.commission
        self.positions[c] += fill.delta_units
        self._day_comm += fill.commission
        self._day_slip += fill.slippage
        self._trades.append({
            "date": fill.timestamp, "instrument": c,
            "side": "buy" if fill.delta_units > 0 else "sell", "units": fill.delta_units,
            "ref_price": fill.ref_price, "fill_price": fill.fill_price,
            "commission": fill.commission, "slippage": fill.slippage, "margined": fill.margined,
        })

    def record(self, timestamp: pd.Timestamp) -> None:
        """End of heartbeat: carry income, re-mark, record the row, and seal the lag buffer."""
        if self.carry is not None and timestamp in self.carry.index:
            row = self.carry.loc[timestamp]
            for c in self.cols:
                rate = row[c] if c in row.index else np.nan
                if self.positions[c] and rate == rate and self.last_px[c] == self.last_px[c]:
                    inc = self.positions[c] * self.last_px[c] * self.instr[c].contract_multiplier * float(rate)
                    self.cash += inc
                    self._day_carry += inc

        equity_post = self.cash + self._cash_holdings()
        ret = equity_post / self.prev_equity - 1.0 if self.prev_equity else 0.0
        self.prev_equity = equity_post

        gross = sum(abs(self._notional(c)) for c in self.cols)
        net = sum(self._notional(c) for c in self.cols)

        self._idx.append(timestamp)
        self._eq.append(equity_post); self._cash_l.append(self.cash); self._ret.append(ret)
        self._gross.append(gross / equity_post if equity_post else 0.0)
        self._net.append(net / equity_post if equity_post else 0.0)
        self._to.append(self._day_traded / self._equity_pre if self._equity_pre else 0.0)
        self._comm.append(self._day_comm); self._slip.append(self._day_slip)
        self._fin.append(self._day_fin); self._carry_l.append(self._day_carry)
        self._pos_snaps.append(dict(self.positions))
        self._wt_snaps.append({c: self._notional(c) / equity_post if equity_post else 0.0 for c in self.cols})

        # one buffer entry per heartbeat (target this bar produced, executed signal_lag bars later)
        self._signal_history.append(self._current_signal)
        self._current_signal = None

    def result(self) -> BacktestResult:
        idx = self._idx
        s = lambda data: pd.Series(data, index=idx)  # noqa: E731
        commission, slippage = s(self._comm), s(self._slip)
        financing, carry_s = s(self._fin), s(self._carry_l)
        return BacktestResult(
            equity_curve=s(self._eq), returns=s(self._ret),
            weights=pd.DataFrame(self._wt_snaps, index=idx),
            turnover=s(self._to), costs=commission + slippage + financing - carry_s,
            meta={"universe": self.universe_name, "n_instruments": len(self.cols),
                  "execution": self.exec.__dict__, "initial_equity": self.initial_equity,
                  "engine": "event_driven"},
            cash=s(self._cash_l), gross_exposure=s(self._gross), net_exposure=s(self._net),
            commission=commission, slippage=slippage, financing=financing, carry=carry_s,
            positions=pd.DataFrame(self._pos_snaps, index=idx),
            trades=pd.DataFrame(self._trades),
        )

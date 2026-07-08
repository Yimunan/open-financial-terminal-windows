"""Granular daily backtest engine — multi-asset incl. FICC.

Daily/swing frequency, run as a per-day accounting loop in *position-and-cash space* (not a
``(weights × returns).sum()`` shortcut), so the simulation reflects real-book mechanics and
stays consistent with what the paper loop can execute. Per day, in order:

1. **Variation margin** — for *margined* instruments (futures/perp/forward/swap), the
   overnight price move flows to cash as VM; the notional is never carried on the balance
   sheet. For *cash-funded* instruments (spot, bonds, ETFs), value is carried as holdings.
2. **Financing** — short-borrow, leverage financing, cash interest on the book.
3. **Rebalance** — target weight → target **units** via a risk-based ``SizingModel``
   (notional for most, DV01 for rates/credit), rounded to lot/contract; trade only the delta
   from the drifted position, gated by a no-trade band.
4. **Fill** — at close / next-open, at an adverse slippage price; commission per asset class.
   Margined fills debit only commission + same-day VM; cash fills debit full notional.
5. **Carry** — coupon accrual (rates), funding (perp), roll/swap-point carry (FX/commodity)
   as a *return* component: ``units · price · multiplier · daily_carry_rate`` → cash.
6. **Re-mark** — realized equity = cash + cash-funded holdings (margined PnL already in cash).

Look-ahead guard: a signal dated *t* governs trading on *t + signal_lag*.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from qhfi.backtest.costs import CompositeCostModel, CostModel
from qhfi.backtest.fills import FillTiming, SlippageModel
from qhfi.backtest.financing import FinancingModel
from qhfi.core.types import Panel, TargetWeights, Universe
from qhfi.portfolio.sizing import CompositeSizing, SizingModel


@dataclass
class ExecutionConfig:
    signal_lag: int = 1
    fill: FillTiming = FillTiming.CLOSE
    # No-trade band: skip a per-name trade whose notional move is below this fraction of equity.
    # The in-code default stays neutral (0.0) for the frictionless equivalence anchors; the
    # *deployment* value is calibrated in config/settings.yaml and loaded via
    # core.config.backtest_execution_config(). See scripts/tune_rebalance.py for the calibration.
    rebalance_threshold: float = 0.0
    allow_fractional: bool = False


@dataclass
class BacktestResult:
    """Everything the evaluation layer needs to grade a run, plus a full execution audit."""

    equity_curve: pd.Series
    returns: pd.Series
    weights: TargetWeights      # realized end-of-day economic weights (notional / equity)
    turnover: pd.Series
    costs: pd.Series            # commission + slippage + financing − carry
    meta: dict
    cash: pd.Series
    gross_exposure: pd.Series
    net_exposure: pd.Series
    commission: pd.Series
    slippage: pd.Series
    financing: pd.Series
    carry: pd.Series            # carry income (positive = earned)
    positions: pd.DataFrame
    trades: pd.DataFrame

    @property
    def positions_weights(self) -> TargetWeights:
        return self.weights


def _round_to_lot(units: float, lot: float) -> float:
    if lot <= 0:
        return units
    return round(units / lot) * lot


class BacktestEngine:
    def __init__(
        self,
        cost_model: CostModel | None = None,
        slippage: SlippageModel | None = None,
        financing: FinancingModel | None = None,
        sizing: SizingModel | None = None,
        execution: ExecutionConfig | None = None,
        initial_equity: float = 100_000.0,
    ) -> None:
        self.cost_model = cost_model or CompositeCostModel()
        self.slippage = slippage or SlippageModel()
        self.financing = financing or FinancingModel()
        self.sizing = sizing or CompositeSizing()
        self.exec = execution or ExecutionConfig()
        self.initial_equity = initial_equity

    def run(
        self,
        weights: TargetWeights,
        prices: Panel,
        universe: Universe,
        open_prices: Panel | None = None,
        carry: Panel | None = None,
    ) -> BacktestResult:
        """Simulate ``weights`` vs the close ``prices`` panel. ``open_prices`` enables
        next-open fills; ``carry`` is a panel of *daily* carry rates (fraction of notional)
        per instrument (coupon/funding/roll)."""
        prices = prices.sort_index()
        cols = list(prices.columns)
        instr = {c: universe.by_id(c) for c in cols}

        target = weights.reindex(index=prices.index, columns=cols).shift(self.exec.signal_lag)

        positions: dict[str, float] = {c: 0.0 for c in cols}
        last_px: dict[str, float] = {c: np.nan for c in cols}   # latest valid close (marking)
        prev_mark: dict[str, float] = {c: np.nan for c in cols} # VM basis for margined
        cash = self.initial_equity
        prev_equity = self.initial_equity

        idx: list[pd.Timestamp] = []
        eq_l, cash_l, ret_l, gross_l, net_l, to_l = [], [], [], [], [], []
        comm_l, slip_l, fin_l, carry_l = [], [], [], []
        pos_snaps: list[dict[str, float]] = []
        wt_snaps: list[dict[str, float]] = []
        trades: list[dict] = []

        for t in prices.index:
            px = prices.loc[t]
            op = open_prices.loc[t] if open_prices is not None else None
            carry_row = carry.loc[t] if (carry is not None and t in carry.index) else None

            def mult(c: str) -> float:
                return instr[c].contract_multiplier

            # 1. Variation margin on margined positions held overnight; refresh marks.
            for c in cols:
                p = px[c]
                if p != p:  # NaN → not trading today; carry position, no remark
                    continue
                p = float(p)
                if instr[c].is_margined and positions[c] and prev_mark[c] == prev_mark[c]:
                    cash += positions[c] * (p - prev_mark[c]) * mult(c)
                prev_mark[c] = p
                last_px[c] = p

            def notional(c: str) -> float:
                lp = last_px[c]
                return positions[c] * lp * mult(c) if (lp == lp and positions[c]) else 0.0

            def cash_holdings() -> float:
                # Only cash-funded instruments sit on the balance sheet; margined PnL is in cash.
                return sum(notional(c) for c in cols if not instr[c].is_margined)

            # 2. Equity (pre-trade) + financing on the economic book.
            equity_pre = cash + cash_holdings()
            long_notional = sum(v for c in cols if (v := notional(c)) > 0)
            short_notional = -sum(v for c in cols if (v := notional(c)) < 0)
            fin = self.financing.daily_carry(cash, equity_pre, long_notional, short_notional)
            cash -= fin

            # 3-4. Rebalance toward target (instruments that trade today only).
            day_comm = day_slip = day_traded = 0.0
            tw = target.loc[t]
            if tw.notna().any():
                for c in cols:
                    price_c = px[c]
                    if price_c != price_c:
                        continue
                    ins = instr[c]
                    w = tw[c]
                    w = 0.0 if w != w else float(w)
                    if w < 0 and not ins.shortable:
                        w = 0.0

                    target_units = self.sizing.target_units(ins, w, equity_pre, float(price_c))
                    if not self.exec.allow_fractional:
                        target_units = _round_to_lot(target_units, ins.lot_size)
                    delta = target_units - positions[c]
                    if delta == 0:
                        continue
                    denom = price_c * ins.contract_multiplier
                    if abs(delta * denom) < self.exec.rebalance_threshold * equity_pre:
                        continue

                    ref = float(price_c)
                    if self.exec.fill is FillTiming.NEXT_OPEN and op is not None and op[c] == op[c]:
                        ref = float(op[c])
                    side = 1 if delta > 0 else -1
                    fill = self.slippage.fill_price(ref, side)
                    notional_traded = abs(delta) * fill * ins.contract_multiplier
                    commission = self.cost_model.cost(notional_traded, ins, fill)
                    slip_cost = abs(delta) * abs(fill - ref) * ins.contract_multiplier

                    if ins.is_margined:
                        # No notional debit; realize same-day VM (close vs fill) + commission.
                        cash += delta * (float(price_c) - fill) * ins.contract_multiplier
                        cash -= commission
                    else:
                        cash -= delta * fill * ins.contract_multiplier
                        cash -= commission

                    positions[c] = target_units
                    day_comm += commission
                    day_slip += slip_cost
                    day_traded += abs(delta) * ref * ins.contract_multiplier
                    trades.append({
                        "date": t, "instrument": c, "side": "buy" if side > 0 else "sell",
                        "units": delta, "ref_price": ref, "fill_price": fill,
                        "commission": commission, "slippage": slip_cost,
                        "margined": ins.is_margined,
                    })

            # 5. Carry income on end-of-day positions (coupon / funding / roll).
            day_carry = 0.0
            if carry_row is not None:
                for c in cols:
                    rate = carry_row[c] if c in carry_row.index else np.nan
                    if positions[c] and rate == rate and last_px[c] == last_px[c]:
                        inc = positions[c] * last_px[c] * mult(c) * float(rate)
                        cash += inc
                        day_carry += inc

            # 6. Re-mark → realized equity & return.
            equity_post = cash + cash_holdings()
            ret = equity_post / prev_equity - 1.0 if prev_equity else 0.0
            prev_equity = equity_post

            gross = sum(abs(notional(c)) for c in cols)
            net = sum(notional(c) for c in cols)
            idx.append(t)
            eq_l.append(equity_post); cash_l.append(cash); ret_l.append(ret)
            gross_l.append(gross / equity_post if equity_post else 0.0)
            net_l.append(net / equity_post if equity_post else 0.0)
            to_l.append(day_traded / equity_pre if equity_pre else 0.0)
            comm_l.append(day_comm); slip_l.append(day_slip); fin_l.append(fin); carry_l.append(day_carry)
            pos_snaps.append(dict(positions))
            wt_snaps.append({c: notional(c) / equity_post if equity_post else 0.0 for c in cols})

        s = lambda data: pd.Series(data, index=idx)  # noqa: E731
        commission = s(comm_l); slippage = s(slip_l); financing = s(fin_l); carry_s = s(carry_l)
        return BacktestResult(
            equity_curve=s(eq_l),
            returns=s(ret_l),
            weights=pd.DataFrame(wt_snaps, index=idx),
            turnover=s(to_l),
            costs=commission + slippage + financing - carry_s,
            meta={"universe": universe.name, "n_instruments": len(cols),
                  "execution": self.exec.__dict__, "initial_equity": self.initial_equity},
            cash=s(cash_l),
            gross_exposure=s(gross_l),
            net_exposure=s(net_l),
            commission=commission,
            slippage=slippage,
            financing=financing,
            carry=carry_s,
            positions=pd.DataFrame(pos_snaps, index=idx),
            trades=pd.DataFrame(trades),
        )

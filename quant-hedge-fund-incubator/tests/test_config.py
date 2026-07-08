"""Guard the settings.yaml -> typed-config wiring so the tunable knobs can't silently revert
to being documentation-only (the state the rebalancing research flagged: the band was listed
in yaml but never loaded, so it sat dormant at 0.0).
"""

from __future__ import annotations

from qhfi.backtest.fills import FillTiming
from qhfi.core.config import (
    backtest_execution_config,
    construction_config,
    load_yaml_settings,
    scorecard_thresholds,
)
from qhfi.evaluation.scorecard import Scorecard


def test_execution_config_loads_band_from_yaml():
    raw = load_yaml_settings()["backtest"]
    cfg = backtest_execution_config()
    # Wiring is live: the loaded band equals the yaml value and is no longer the dormant 0.0.
    assert cfg.rebalance_threshold == raw["rebalance_threshold"]
    assert cfg.rebalance_threshold > 0.0
    assert cfg.fill is FillTiming.CLOSE          # string -> enum coercion
    assert cfg.signal_lag == raw["signal_lag"]
    # Non-ExecutionConfig keys in the block (initial_equity, periods_per_year) must not leak in.
    assert not hasattr(cfg, "initial_equity")


def test_construction_config_loads_smoothing_from_yaml():
    raw = load_yaml_settings()["construction"]
    cfg = construction_config()
    assert cfg.smoothing_halflife == raw["smoothing_halflife"]
    assert cfg.max_position == raw["max_position"]


def test_scorecard_thresholds_load_from_yaml():
    raw = load_yaml_settings()["scorecard"]
    t = scorecard_thresholds()
    assert t.max_ann_turnover == raw["max_ann_turnover"]
    assert t.min_sharpe == raw["min_sharpe"]
    # The convenience constructor uses the same loaded thresholds.
    assert Scorecard.from_config().t.max_ann_turnover == raw["max_ann_turnover"]

"""Guard test: fundamental factors must fail loudly on a universe with no equities.

Without the guard, value/quality on an all-crypto/fx/commodity universe silently returns a flat,
holding-less backtest (empty fundamentals panel → all-NaN scores → zero weights). These run with
no network: the equity guard fires before any fundamentals fetch.
"""

from __future__ import annotations

import pandas as pd
import pytest
from qhfi.core.types import Universe

from app.deps import make_instrument
from app.services import factors as fac

_FUNDAMENTAL = ["value", "value_bp", "quality", "quality_gp"]


def _crypto_universe() -> Universe:
    return Universe(name="_c", instruments=[make_instrument("BTC/USDT", "crypto"),
                                            make_instrument("ETH/USDT", "crypto")])


@pytest.mark.parametrize("factor_key", _FUNDAMENTAL)
def test_fundamental_factor_on_crypto_raises_clear_error(factor_key):
    with pytest.raises(ValueError, match="equity fundamentals"):
        fac.build_signed(None, None, None, _crypto_universe(), pd.DataFrame(), factor_key)


def test_guard_does_not_overtrigger_on_a_universe_with_equities(monkeypatch):
    """A universe containing equities passes the guard and proceeds to the fundamentals fetch."""
    uni = Universe(name="_m", instruments=[make_instrument("AAPL", "equity"),
                                           make_instrument("BTC/USDT", "crypto")])

    def sentinel(*a, **k):
        raise RuntimeError("reached the fundamentals fetch")

    monkeypatch.setattr(fac, "ensure_fundamentals", sentinel)
    # Not a ValueError about fundamentals → we got past the guard into the fetch path.
    with pytest.raises(RuntimeError, match="reached the fundamentals fetch"):
        fac.build_signed(object(), object(), object(), uni, pd.DataFrame(), "value")

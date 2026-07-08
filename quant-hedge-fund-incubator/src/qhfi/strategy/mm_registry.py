"""Registry for market-making (quoting) strategies.

Kept separate from the vectorized ``strategy.registry`` (which is typed to ``Strategy`` /
``generate_weights``) so the incompatible ``QuotingStrategy`` interface doesn't leak into
walk-forward / multi-alpha tooling. Same shape: class name → class, ``register`` / ``get`` /
``available``.
"""

from __future__ import annotations

from qhfi.backtest.eventdriven.strategy import QuotingStrategy

_REGISTRY: dict[str, type[QuotingStrategy]] = {}


def register(cls: type[QuotingStrategy]) -> type[QuotingStrategy]:
    """Register a ``QuotingStrategy`` subclass under its ``name`` (or class name)."""
    key = getattr(cls, "name", "") or cls.__name__
    _REGISTRY[key] = cls
    return cls


def get(name: str) -> type[QuotingStrategy]:
    if name not in _REGISTRY:
        raise KeyError(f"unknown market-making strategy {name!r}; available: {available()}")
    return _REGISTRY[name]


def available() -> list[str]:
    return sorted(_REGISTRY)


# Built-ins.
from qhfi.strategy.library.mm.alpha_quoter import AlphaQuoterMM  # noqa: E402
from qhfi.strategy.library.mm.avellaneda_stoikov import AvellanedaStoikovMM  # noqa: E402
from qhfi.strategy.library.mm.linear_inventory import LinearInventoryMM  # noqa: E402
from qhfi.strategy.library.mm.symmetric import SymmetricMM  # noqa: E402

register(AvellanedaStoikovMM)
register(LinearInventoryMM)
register(SymmetricMM)
register(AlphaQuoterMM)

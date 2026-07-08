"""Strategy discovery/registration.

The decorator records strategy classes by name so the CLI, codegen agent, and registry can
instantiate them from a string. Importing ``qhfi.strategy.library`` registers built-ins.
"""

from __future__ import annotations

from qhfi.strategy.base import Strategy

_REGISTRY: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    key = cls.name or cls.__name__
    if key in _REGISTRY:
        raise ValueError(f"duplicate strategy name: {key!r}")
    _REGISTRY[key] = cls
    return cls


def get(name: str) -> type[Strategy]:
    if name not in _REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; known: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def all_names() -> list[str]:
    return sorted(_REGISTRY)

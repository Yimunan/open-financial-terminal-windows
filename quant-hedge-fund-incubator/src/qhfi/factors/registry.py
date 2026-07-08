"""Factor discovery/registration — mirrors strategy.registry.

Importing ``qhfi.factors.library`` registers the built-in factors so the CLI, the codegen
agent, and factor-combination strategies can instantiate them from a string key.
"""

from __future__ import annotations

from qhfi.factors.base import Factor

_REGISTRY: dict[str, type[Factor]] = {}


def register(cls: type[Factor]) -> type[Factor]:
    key = cls.name or cls.__name__
    if key in _REGISTRY:
        raise ValueError(f"duplicate factor name: {key!r}")
    _REGISTRY[key] = cls
    return cls


def get(name: str) -> type[Factor]:
    if name not in _REGISTRY:
        raise KeyError(f"unknown factor {name!r}; known: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def all_names() -> list[str]:
    return sorted(_REGISTRY)

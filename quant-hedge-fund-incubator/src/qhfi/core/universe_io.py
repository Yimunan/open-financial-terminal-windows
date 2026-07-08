"""Load/serialize a Universe from a YAML config (config/instruments/*.yaml).

Each instrument entry is a dict matching the ``Instrument`` model; nested ``equity:`` is
coerced to ``EquityMeta`` by pydantic. This makes a stock pool a versioned, reviewable
artifact rather than a literal buried in a script — and is what the ``qhfi data pull
--universe <yaml>`` CLI consumes.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from qhfi.core.types import Instrument, Universe


def load_universe(path: str | Path) -> Universe:
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    instruments = [Instrument(**entry) for entry in data["instruments"]]
    return Universe(name=data.get("name", path.stem), instruments=instruments)


def save_universe(universe: Universe, path: str | Path) -> None:
    payload = {
        "name": universe.name,
        "instruments": [i.model_dump(exclude_none=True, mode="json") for i in universe.instruments],
    }
    Path(path).write_text(yaml.safe_dump(payload, sort_keys=False))

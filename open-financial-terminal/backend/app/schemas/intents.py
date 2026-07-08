"""Backend mirror of the frontend interaction vocabulary (`frontend/src/state/intents.ts`).

This is the server-side source of truth for the terminal's typed `Intent` / `ChannelContext` /
`SendPayload` shapes. The frontend keeps its own TypeScript copy (the two are hand-synced, like the
rest of the Pydantic↔TS contracts); `tests/test_intent_parity.py` reads both and fails if they
drift, so the sync is enforced rather than hoped-for.

Only the `send`-result resolver (`routers/intents.py`) consumes these at runtime today; the rest
exist so the contract is declared once and validated on both sides.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field

Asset = Literal["equity", "crypto"]
LinkChannel = Literal["red", "blue", "green"]


class DateRange(BaseModel):
    start: str
    end: str


class ChannelContext(BaseModel):
    """Superset of the historical {symbol, asset} link payload — the richer context that travels
    with a channel. Field names MUST match the TS `ChannelContext` (+ inherited `SymbolRef`)."""

    symbol: str
    asset: Asset
    timeframe: Optional[str] = None
    range: Optional[DateRange] = None
    universe: Optional[str] = None
    factor: Optional[str] = None
    extra: Optional[dict] = None


# ── send payloads (discriminated on `kind`) ──────────────────────────────────────────────────────


class ScreenResultPayload(BaseModel):
    kind: Literal["screen_result"]
    universe: str
    factor: str
    symbols: list[str]
    weights: Optional[dict[str, float]] = None
    asset: Asset


class BacktestResultPayload(BaseModel):
    kind: Literal["backtest_result"]
    strategyKey: Optional[str] = None
    params: dict
    universe: str
    metrics: Optional[dict[str, float]] = None


class SymbolsPayload(BaseModel):
    kind: Literal["symbols"]
    symbols: list[str]
    asset: Asset


SendPayload = Annotated[
    Union[ScreenResultPayload, BacktestResultPayload, SymbolsPayload],
    Field(discriminator="kind"),
]

SEND_PAYLOAD_KINDS: tuple[str, ...] = ("screen_result", "backtest_result", "symbols")


# ── intents (discriminated on `kind`) ────────────────────────────────────────────────────────────


class OpenIntent(BaseModel):
    kind: Literal["open"]
    widget: str
    params: Optional[dict] = None


class SetContextIntent(BaseModel):
    kind: Literal["set_context"]
    channel: LinkChannel
    context: ChannelContext


class ConfigureIntent(BaseModel):
    kind: Literal["configure"]
    panelId: str
    params: dict


class SendIntent(BaseModel):
    kind: Literal["send"]
    target: str
    payload: SendPayload
    open: bool = False


class SwitchWorkspaceIntent(BaseModel):
    kind: Literal["switch_workspace"]
    name: str


class ApplyTemplateIntent(BaseModel):
    kind: Literal["apply_template"]
    name: str


class ReadWorkspaceIntent(BaseModel):
    kind: Literal["read_workspace"]


class NotifyIntent(BaseModel):
    kind: Literal["notify"]
    level: Literal["info", "warn", "error"]
    message: str


Intent = Annotated[
    Union[
        OpenIntent,
        SetContextIntent,
        ConfigureIntent,
        SendIntent,
        SwitchWorkspaceIntent,
        ApplyTemplateIntent,
        ReadWorkspaceIntent,
        NotifyIntent,
    ],
    Field(discriminator="kind"),
]

INTENT_KINDS: tuple[str, ...] = (
    "open",
    "set_context",
    "configure",
    "send",
    "switch_workspace",
    "apply_template",
    "read_workspace",
    "notify",
)

#: The subset of intents the Assistant control loop may drive. Mirrors the frontend's legacy
#: `CLIENT_ACTIONS` verbs (assistant_agent.CLIENT_ACTIONS) under the new vocabulary:
#: open_widget→open, set_symbol→set_context, configure_widget→configure. The mutating `send`/`notify`
#: are deliberately absent — the agent navigates/configures only.
CLIENT_INTENT_KINDS: tuple[str, ...] = (
    "open",
    "set_context",
    "configure",
    "switch_workspace",
    "apply_template",
    "read_workspace",
)

#: Maps the legacy Assistant action verbs to their intent kind. The parity test asserts this stays
#: aligned with `assistant_agent.CLIENT_ACTIONS`.
CLIENT_ACTION_TO_INTENT: dict[str, str] = {
    "open_widget": "open",
    "set_symbol": "set_context",
    "configure_widget": "configure",
    "switch_workspace": "switch_workspace",
    "apply_template": "apply_template",
    "read_workspace": "read_workspace",
}

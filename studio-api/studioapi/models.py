"""Studio ORM.

`User` maps a Google-authenticated email to its platform tenant/project/key
(held server-side, never sent to the browser). `Conversation`/`Message` store
chat history. `Agent`/`Prompt` back the agent builder + prompt library.
`Watchlist`/`WatchlistItem` are the user's @groups (U1). `Integration` is the
messenger seam (F3).
"""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from studioapi.db import Base


def _uid(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


class User(Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(256), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48))
    project_id: Mapped[str] = mapped_column(String(48))
    api_key: Mapped[str] = mapped_column(String(80))  # the tenant platform key (server-side only)
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)  # completed the F1 onboarding
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: _uid("cnv"))
    user_email: Mapped[str] = mapped_column(ForeignKey("users.email"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New chat")
    agent_id: Mapped[str | None] = mapped_column(String(48), nullable=True)  # which agent drove this chat
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --- F1: configurable agents ----------------------------------------------
class Agent(Base):
    """A configurable agent. ``user_email is None`` marks a provided template
    (a starting point any user can pick or clone). ``data_sources`` is a JSON
    list of connector ids (or full tool names) the agent may use."""

    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: _uid("agt"))
    user_email: Mapped[str | None] = mapped_column(index=True, nullable=True)  # null = provided template
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(280), nullable=True)
    model: Mapped[str] = mapped_column(String(64), default="gemini")  # gemini-only (invariant #7)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_sources: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of connector ids
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Prompt(Base):
    """A reusable prompt. ``community`` rows (``user_email is None``) are the
    seeded public catalog; a user imports one to get an editable personal copy
    (``source_id`` records where it came from)."""

    __tablename__ = "prompts"
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: _uid("prm"))
    user_email: Mapped[str | None] = mapped_column(index=True, nullable=True)  # null = community
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(280), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(48), nullable=True)
    community: Mapped[bool] = mapped_column(Boolean, default=False)
    source_id: Mapped[str | None] = mapped_column(String(48), nullable=True)  # imported-from community id
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --- U1: watchlists / @groups ---------------------------------------------
class Watchlist(Base):
    """A user's named group of companies (the ``@handle`` that chat + the analyst
    builder tag). ``name`` is unique per user and doubles as the @-handle."""

    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("user_email", "name", name="uq_watchlist_user_name"),)
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: _uid("wl"))
    user_email: Mapped[str] = mapped_column(ForeignKey("users.email"), index=True)
    name: Mapped[str] = mapped_column(String(80))  # the @handle, e.g. "반도체바스켓"
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class WatchlistItem(Base):
    """One company in a watchlist. A company may belong to many watchlists, so the
    uniqueness is per (watchlist, market, ticker), not global."""

    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("watchlist_id", "market", "ticker", name="uq_item_watchlist_market_ticker"),
    )
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: _uid("wli"))
    watchlist_id: Mapped[str] = mapped_column(ForeignKey("watchlists.id"), index=True)
    market: Mapped[str] = mapped_column(String(8))  # US | KR
    ticker: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Board(Base):
    """A named canvas the user pins assets onto (charts, sources, text). Users can keep
    several; pinning offers a board picker. Notion-like free layout per item."""

    __tablename__ = "boards"
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: _uid("brd"))
    user_email: Mapped[str] = mapped_column(ForeignKey("users.email"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PinnedArtifact(Base):
    """An asset the user pinned to a Board — a live artifact (chart/table), a source/evidence
    card (``spec.kind == 'source'``), or a text block (``spec.kind == 'text'``). ``spec`` is the
    JSON; chart pins are re-fetchable via tool+args (refresh). ``x/y/w/h`` are the Notion-like
    canvas layout (null until placed)."""

    __tablename__ = "pinned_artifacts"
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: _uid("pin"))
    user_email: Mapped[str] = mapped_column(ForeignKey("users.email"), index=True)
    # which board this asset lives on (nullable for legacy rows → treated as the default board)
    board_id: Mapped[str | None] = mapped_column(ForeignKey("boards.id"), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    spec: Mapped[str] = mapped_column(Text)  # JSON asset spec
    # free-canvas layout (px); null = not yet placed (web auto-flows it)
    x: Mapped[int | None] = mapped_column(Integer, nullable=True)
    y: Mapped[int | None] = mapped_column(Integer, nullable=True)
    w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Integration(Base):
    __tablename__ = "integrations"
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: _uid("int"))
    user_email: Mapped[str] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(24))  # telegram | slack
    config: Mapped[str] = mapped_column(Text)  # JSON (tokens/channels)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --- F3: notification alerts (board / widget scoped push) ------------------
class NotificationAlert(Base):
    """A standing watcher the user enables from the DASHBOARD — either for a whole board
    (``scope='board'`` → the board's key widgets) or a single widget (``scope='widget'`` →
    bound to one ``pin_id``). It evaluates ``trigger_type`` over ``params`` on a ``schedule``,
    and when due renders a SOURCED message (source · as_of · deep link — never advice/forecast)
    pushed to ``channels``. ``source_spec`` remembers the widget's tool+args so threshold
    triggers re-fetch live data and the message links back to the dashboard/explore evidence."""

    __tablename__ = "notification_alerts"
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: _uid("alt"))
    user_email: Mapped[str] = mapped_column(ForeignKey("users.email"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    scope: Mapped[str] = mapped_column(String(8), default="board")  # board | widget
    board_id: Mapped[str | None] = mapped_column(String(48), index=True, nullable=True)
    pin_id: Mapped[str | None] = mapped_column(String(48), index=True, nullable=True)  # widget scope
    # earnings | rate | macro_indicator | filing_news | price_threshold | digest
    trigger_type: Mapped[str] = mapped_column(String(24))
    params: Mapped[str | None] = mapped_column(Text, nullable=True)      # JSON {target, threshold, ...}
    schedule: Mapped[str | None] = mapped_column(Text, nullable=True)    # JSON {freq, time?, every_minutes?}
    channels: Mapped[str] = mapped_column(Text, default="[]")            # JSON list of channel kinds
    quiet_hours: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON {start,end}
    status: Mapped[str] = mapped_column(String(8), default="active")     # active | paused
    source_spec: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON {tool, args, source, deeplink}
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NotificationDelivery(Base):
    """One message an alert pushed to one channel. Carries the full trust envelope
    (title/body/as_of/source/deeplink). ``status`` = sent | simulated | failed — ``simulated``
    means the channel had no credentials, so we recorded the message we *would* have sent (keeps
    the alert→delivery flow verifiable end-to-end without external accounts)."""

    __tablename__ = "notification_deliveries"
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: _uid("dlv"))
    alert_id: Mapped[str] = mapped_column(ForeignKey("notification_alerts.id"), index=True)
    user_email: Mapped[str] = mapped_column(index=True)  # denormalized for ownership scoping
    channel: Mapped[str] = mapped_column(String(24))     # telegram | slack | kakao | email
    title: Mapped[str] = mapped_column(String(240))
    body: Mapped[str] = mapped_column(Text)
    as_of: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    deeplink: Mapped[str | None] = mapped_column(String(240), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="sent")  # sent | simulated | failed
    sent_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChannelConnection(Base):
    """A user's connection to one messenger (BYO bot-token / webhook). Credentials live in
    ``config`` server-side only — never sent to the browser; the API exposes only whether
    credentials exist + ``verified``. One row per (user, channel)."""

    __tablename__ = "channel_connections"
    __table_args__ = (UniqueConstraint("user_email", "channel", name="uq_channel_user_kind"),)
    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: _uid("chn"))
    user_email: Mapped[str] = mapped_column(index=True)
    channel: Mapped[str] = mapped_column(String(24))  # telegram | slack | kakao | email
    config: Mapped[str] = mapped_column(Text, default="{}")  # JSON creds (server-side only)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DashboardTemplate(Base):
    """A provided dashboard template (seeded, global). ``widgets`` is a JSON list of
    {spec(tool+args+viz+source), x, y, w, h} that ``POST /board/from-template`` materializes
    as pins on the user's chosen board."""

    __tablename__ = "dashboard_templates"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)  # stable seed id (e.g. "dt_semi")
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(280), nullable=True)
    market: Mapped[str | None] = mapped_column(String(8), nullable=True)  # US | KR | None(both)
    widgets: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of {spec,x,y,w,h}
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

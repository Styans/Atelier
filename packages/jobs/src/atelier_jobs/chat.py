"""SQLite tables of the chat assistant: messages, streamed turn events and proposals (ADR-036).

They live in the same file as the job queue: the API appends the user's message and reads the
history, the worker appends the assistant's answer and streams its events, and the API relays
those to the browser by polling ``chat_events``. Numbering (``seq``) is done inside one
INSERT ... SELECT, so the two processes never hand out the same number.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from sqlalchemy import UniqueConstraint, literal_column, text
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, col, select

from atelier_jobs.queue import JobQueue

ROLES = ("user", "assistant", "notice")
BLOCK_TYPES = ("text", "tool_use", "tool_result")
EVENT_KINDS = ("text", "tool_start", "tool_result", "status", "done", "error")
PROPOSAL_STATUSES = ("pending", "applied", "dismissed")


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("show_id", "seq"),)

    id: int | None = Field(default=None, primary_key=True)
    show_id: str = Field(index=True)
    seq: int  # per show, from 1
    role: str  # user assistant notice
    content: str = "[]"  # JSON list of provider-neutral blocks: text / tool_use / tool_result
    turn_job_id: str | None = Field(default=None, index=True)
    revision_before: int | None = None  # what a rollback of this answer returns to
    revision_after: int | None = None
    created_at: float = Field(default_factory=time.time)

    def blocks(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], json.loads(self.content))

    def public(self) -> dict[str, Any]:
        data = self.model_dump()
        data["content"] = self.blocks()
        return data


class ChatEvent(SQLModel, table=True):
    __tablename__ = "chat_events"

    id: int | None = Field(default=None, primary_key=True)  # the SSE cursor
    turn_job_id: str = Field(index=True)
    seq: int  # per turn, from 1
    kind: str  # text tool_start tool_result status done error
    payload: str = "{}"  # JSON
    created_at: float = Field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        data = self.model_dump()
        data["payload"] = json.loads(self.payload)
        return data


class Proposal(SQLModel, table=True):
    """An expensive action the assistant suggests; it runs only when the user applies it."""

    __tablename__ = "proposals"

    id: str = Field(primary_key=True)
    show_id: str = Field(index=True)
    message_id: int | None = None
    action: str  # points compile validate export generate_images generate_mesh remove_* ...
    args: str = "{}"  # JSON
    summary: str = ""
    status: str = Field(default="pending", index=True)  # pending applied dismissed
    created_at: float = Field(default_factory=time.time)
    decided_at: float | None = None

    def public(self) -> dict[str, Any]:
        data = self.model_dump()
        data["args"] = json.loads(self.args)
        return data


_TABLES = [SQLModel.metadata.tables[name] for name in ("chat_messages", "chat_events", "proposals")]


class ChatStore:
    def __init__(self, source: JobQueue | Engine) -> None:
        self.engine: Engine = source.engine if isinstance(source, JobQueue) else source
        SQLModel.metadata.create_all(self.engine, tables=_TABLES)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine, expire_on_commit=False) as session:
            yield session

    # --- messages ---

    def append(
        self,
        show_id: str,
        role: str,
        content: list[dict[str, Any]],
        turn_job_id: str | None = None,
        revision_before: int | None = None,
        revision_after: int | None = None,
    ) -> ChatMessage:
        if role not in ROLES:
            raise ValueError(f"unknown chat role {role!r}; known: {', '.join(ROLES)}")
        for block in content:
            if block.get("type") not in BLOCK_TYPES:
                raise ValueError(
                    f"unknown content block {block!r}; types: {', '.join(BLOCK_TYPES)}"
                )
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "INSERT INTO chat_messages (show_id, seq, role, content, turn_job_id, "
                    "revision_before, revision_after, created_at) "
                    "SELECT :show_id, COALESCE(MAX(seq), 0) + 1, :role, :content, :turn, "
                    ":before, :after, :now FROM chat_messages WHERE show_id = :show_id "
                    "RETURNING id"
                ),
                {
                    "show_id": show_id,
                    "role": role,
                    "content": json.dumps(content, ensure_ascii=False),
                    "turn": turn_job_id,
                    "before": revision_before,
                    "after": revision_after,
                    "now": time.time(),
                },
            ).one()
        return self._message(row[0])

    def _message(self, message_id: int) -> ChatMessage:
        with self.session() as session:
            message = session.get(ChatMessage, message_id)
        if message is None:
            raise KeyError(f"no chat message {message_id}")
        return message

    def history(self, show_id: str, limit: int | None = None) -> list[ChatMessage]:
        """Messages of a show in ``seq`` order; with ``limit``, only the latest ones."""
        with self.session() as session:
            query = select(ChatMessage).where(ChatMessage.show_id == show_id)
            if limit is None:
                return list(session.exec(query.order_by(col(ChatMessage.seq))))
            latest = list(session.exec(query.order_by(col(ChatMessage.seq).desc()).limit(limit)))
        return latest[::-1]

    def set_revision_after(self, message_id: int, revision: int) -> None:
        with self.session() as session:
            message = session.get(ChatMessage, message_id)
            if message is None:
                raise KeyError(f"no chat message {message_id}")
            message.revision_after = revision
            session.add(message)
            session.commit()

    # --- turn events ---

    def emit(self, turn_job_id: str, kind: str, payload: dict[str, Any]) -> ChatEvent:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown chat event {kind!r}; known: {', '.join(EVENT_KINDS)}")
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "INSERT INTO chat_events (turn_job_id, seq, kind, payload, created_at) "
                    "SELECT :turn, COALESCE(MAX(seq), 0) + 1, :kind, :payload, :now "
                    "FROM chat_events WHERE turn_job_id = :turn RETURNING id"
                ),
                {
                    "turn": turn_job_id,
                    "kind": kind,
                    "payload": json.dumps(payload, ensure_ascii=False, default=str),
                    "now": time.time(),
                },
            ).one()
        with self.session() as session:
            return cast(ChatEvent, session.get(ChatEvent, row[0]))

    def events_since(
        self, turn_job_id: str, after_id: int = 0, limit: int = 500
    ) -> list[ChatEvent]:
        """Events of a turn with ``id > after_id``, oldest first; the last id is the next cursor."""
        with self.session() as session:
            query = (
                select(ChatEvent)
                .where(ChatEvent.turn_job_id == turn_job_id)
                .where(col(ChatEvent.id) > after_id)
                .order_by(col(ChatEvent.id))
                .limit(limit)
            )
            return list(session.exec(query))

    # --- proposals ---

    def add_proposal(
        self,
        show_id: str,
        action: str,
        args: dict[str, Any] | None = None,
        summary: str = "",
        message_id: int | None = None,
    ) -> Proposal:
        proposal = Proposal(
            id=f"p_{uuid.uuid4().hex[:12]}",
            show_id=show_id,
            message_id=message_id,
            action=action,
            args=json.dumps(args or {}, ensure_ascii=False),
            summary=summary,
        )
        with self.session() as session:
            session.add(proposal)
            session.commit()
        return proposal

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        with self.session() as session:
            return session.get(Proposal, proposal_id)

    def proposals(self, show_id: str, status: str | None = None) -> list[Proposal]:
        with self.session() as session:
            query = (
                select(Proposal)
                .where(Proposal.show_id == show_id)
                .order_by(literal_column("rowid"))
            )
            if status is not None:
                query = query.where(Proposal.status == status)
            return list(session.exec(query))

    def decide_proposal(self, proposal_id: str, status: str) -> Proposal:
        """Move a pending proposal to ``applied`` or ``dismissed``; a decision is final.

        One conditional UPDATE, so a double click (or the API and the worker together) decides
        it once and the second call gets an error instead of applying it twice.
        """
        if status not in ("applied", "dismissed"):
            raise ValueError(f"a proposal can only be applied or dismissed, not {status!r}")
        with self.engine.begin() as conn:
            updated = conn.execute(
                text(
                    "UPDATE proposals SET status=:status, decided_at=:now "
                    "WHERE id=:id AND status='pending' RETURNING id"
                ),
                {"status": status, "now": time.time(), "id": proposal_id},
            ).first()
        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"no proposal {proposal_id!r}")
        if updated is None:
            raise ValueError(
                f"proposal {proposal_id!r} is already {proposal.status}; only a pending one "
                "can be applied or dismissed"
            )
        return proposal

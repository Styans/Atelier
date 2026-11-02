"""One assistant turn: the model answers, calls tools, sees the results, answers again.

The loop is ours, not the model's: at most MAX_STEPS model calls per turn, and after
MAX_FAILURES failed tool calls in a row the turn stops with an honest notice instead of
guessing on (SPEC §8.5). Every message goes to the chat history as it is produced, so a turn
cut short (cancel, crash) still shows what was done; the revision the turn started from is
kept with its messages, which is what "roll back this answer" returns to.

The show itself is not in the history: a fresh compact summary is added to the request only,
at the end, so the stable prefix (system prompt, tools, earlier turns) stays the same and
the server can reuse it (ADR-040).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from atelier_assistant.llm import (
    LLMClient,
    LLMError,
    Message,
    text_block,
    tool_result_block,
)
from atelier_assistant.tools import TOOLS, ToolContext, run_tool
from atelier_domain import Show
from atelier_jobs import ChatStore
from atelier_jobs.chat import ChatMessage
from atelier_show_engine.service import ShowService
from atelier_show_engine.store import ShowStore

MAX_STEPS = 8
MAX_FAILURES = 2
HISTORY_CHARS = 24_000  # ~8K tokens of the 16K context; older turns are left out
TEXT_FLUSH_S = 0.15

Emit = Callable[[str, dict[str, Any]], None]


def system_prompt() -> str:
    return resources.files("atelier_assistant").joinpath("system_prompt.md").read_text("utf-8")


def _size(message: Message) -> int:
    total = 0
    for block in message["content"]:
        if block["type"] == "text":
            total += len(block["text"])
        elif block["type"] == "tool_use":
            total += len(str(block["input"])) + 40
        else:
            total += len(str(block["content"])) + 40
    return total


def to_messages(history: list[ChatMessage]) -> list[Message]:
    """The chat as the model sees it: notices are for people, rolled-back turns are gone (the
    model would think it had already done what they did); long chats lose their start.

    A cut never leaves a tool result without its call: the kept part starts at a user
    message that is not a tool result.
    """
    messages: list[Message] = [
        {"role": "user" if m.role == "user" else "assistant", "content": m.blocks()}
        for m in history
        if m.role in ("user", "assistant") and m.blocks() and m.rolled_back_at is None
    ]
    total, start = 0, len(messages)
    for index in range(len(messages) - 1, -1, -1):
        total += _size(messages[index])
        if total > HISTORY_CHARS and start < len(messages):
            break
        message = messages[index]
        if message["role"] == "user" and all(b["type"] == "text" for b in message["content"]):
            start = index
    return messages[start:]


def _clock(seconds: float) -> str:
    whole = round(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def _previous_end(history: list[ChatMessage], turn_job_id: str) -> int | None:
    """The revision the previous (not rolled-back) turn left the show at."""
    ends = [
        m.revision_after
        for m in history
        if m.revision_after is not None
        and m.turn_job_id != turn_job_id
        and m.rolled_back_at is None
    ]
    return max(ends) if ends else None


def _user_edits(store: ShowStore, since: int | None) -> list[str]:
    """Revisions made by hand since the previous turn ended.

    The model remembers its own program from the history; without this it "corrects" the
    user's edits back to what it said before.
    """
    if since is None:
        return []
    return [
        info.message
        for info in store.history()
        if info.revision > since and info.author != "assistant"
    ]


def _hand_changed(store: ShowStore, since: int | None) -> dict[str, dict[str, Any]]:
    """Scene and card fields that differ from where the previous turn left them: id → field →
    value now. Only people edit between turns, so every difference is theirs."""
    if since is None or since >= store.current_revision():
        return {}
    then, now = store.load_revision(since), store.load()

    def dumped(show: Show) -> dict[str, dict[str, Any]]:
        items = {s.id: s.model_dump(mode="json") for s in show.program}
        return items | {i: m.model_dump(mode="json") for i, m in show.models.items()}

    old = dumped(then)
    changed: dict[str, dict[str, Any]] = {}
    for item_id, after in dumped(now).items():
        before = old.get(item_id)
        if before is not None:
            fields = {k: v for k, v in after.items() if before.get(k) != v}
            if fields:
                changed[item_id] = fields
    return changed


class _TextBuffer:
    """Streams text deltas to the chat events in small batches, not token by token."""

    def __init__(self, emit: Emit) -> None:
        self.emit = emit
        self.parts: list[str] = []
        self.last = time.monotonic()

    def add(self, delta: str) -> None:
        self.parts.append(delta)
        if time.monotonic() - self.last >= TEXT_FLUSH_S:
            self.flush()

    def flush(self) -> None:
        if self.parts:
            self.emit("text", {"delta": "".join(self.parts)})
            self.parts = []
        self.last = time.monotonic()


@dataclass
class TurnResult:
    revision_before: int
    revision_after: int
    steps: int
    tool_calls: int
    failures: int
    proposals: list[str] = field(default_factory=list)
    stopped: str | None = None  # why the turn ended early, in Russian, or None


class Agent:
    def __init__(self, llm: LLMClient, service: ShowService, chat: ChatStore) -> None:
        self.llm = llm
        # Edits by the assistant are revisions by "assistant", whoever runs the turn.
        self.service = ShowService(service.config, service.registry, author="assistant")
        self.chat = chat
        self.system = system_prompt()
        self.tools = [tool.spec() for tool in TOOLS]

    def run_turn(self, store: ShowStore, show_id: str, turn_job_id: str, emit: Emit) -> TurnResult:
        before = store.current_revision()
        result = TurnResult(before, before, 0, 0, 0)
        failures = 0
        history = self.chat.history(show_id)
        messages = to_messages(history)
        if not messages or messages[-1]["role"] != "user":
            raise LLMError("нет сообщения пользователя для ответа")
        since = _previous_end(history, turn_job_id)
        edits = _user_edits(store, since)
        request_text = " ".join(b["text"] for b in messages[-1]["content"] if b["type"] == "text")
        said = " ".join(
            b["text"]
            for m in history
            if m.role == "user" and m.rolled_back_at is None
            for b in m.blocks()
            if b["type"] == "text"
        )
        ctx = ToolContext(
            self.service,
            store,
            self.chat,
            show_id,
            request=request_text,
            said=said,
            hand_changed=_hand_changed(store, since),
        )

        def save(role: str, content: list[dict[str, Any]]) -> ChatMessage:
            return self.chat.append(
                show_id, role, content, turn_job_id=turn_job_id, revision_before=before
            )

        last_saved: ChatMessage | None = None
        while result.steps < MAX_STEPS:
            result.steps += 1
            emit("status", {"stage": "thinking", "step": result.steps})
            request = [*messages[:-1], self._with_state(messages[-1], store, edits)]
            buffer = _TextBuffer(emit)
            try:
                reply = self.llm.respond(self.system, request, self.tools, on_text=buffer.add)
            finally:
                buffer.flush()
            messages.append(reply.to_message())
            last_saved = save("assistant", reply.content) if reply.content else last_saved
            if reply.stop == "max_tokens":
                result.stopped = "ответ модели оборвался на лимите длины"
                break
            if not reply.tool_uses:
                break
            results = []
            for call in reply.tool_uses:
                result.tool_calls += 1
                emit("tool_start", {"id": call["id"], "name": call["name"], "input": call["input"]})
                outcome = run_tool(ctx, call["name"], call["input"])
                emit(
                    "tool_result",
                    {
                        "id": call["id"],
                        "name": call["name"],
                        "text": outcome.text,
                        "is_error": outcome.is_error,
                    },
                )
                results.append(tool_result_block(call["id"], outcome.text, outcome.is_error))
                if outcome.is_error:
                    failures += 1
                    result.failures += 1
                else:
                    failures = 0
            messages.append({"role": "user", "content": results})
            save("user", results)
            if failures > MAX_FAILURES:
                result.stopped = (
                    "модель трижды подряд вызвала инструменты с ошибками; последняя: "
                    + results[-1]["content"]
                )
                break
        else:
            result.stopped = f"ход прерван после {MAX_STEPS} шагов модели"

        result.proposals = ctx.proposals
        result.revision_after = store.current_revision()
        if result.revision_after != before:
            # The model's own summary of a turn is not to be trusted with numbers: the code
            # says so when the show it left is over budget.
            timeline = self.service.estimate_timeline(store)
            if timeline.over_budget_s >= 1:
                last_saved = self.chat.append(
                    show_id,
                    "notice",
                    [
                        text_block(
                            f"По оценке кода шоу длится {_clock(timeline.total_s)} при бюджете"
                            f" {_clock(timeline.budget_s)}: не укладывается на"
                            f" {timeline.over_budget_s:.0f} с."
                        )
                    ],
                    turn_job_id=turn_job_id,
                    revision_before=before,
                )
        if result.stopped:
            notice = self.chat.append(
                show_id,
                "notice",
                [text_block(f"Не получилось довести ход до конца: {result.stopped}.")],
                turn_job_id=turn_job_id,
                revision_before=before,
            )
            last_saved = notice
        if last_saved is not None:
            self.chat.set_revision_after(last_saved.id or 0, result.revision_after)
        emit(
            "done",
            {
                "revision_before": before,
                "revision_after": result.revision_after,
                "proposals": ctx.proposals,
                "stopped": result.stopped,
            },
        )
        return result

    def _with_state(self, message: Message, store: ShowStore, edits: list[str]) -> Message:
        """The user's message plus the show as it is now (for this request only)."""
        state = self.service.show_summary(store)
        if edits:
            state += (
                "\nПользователь сам поменял после вашего прошлого ответа (это его решения,"
                " не возвращайте их): " + "; ".join(edits)
            )
        return {
            "role": message["role"],
            "content": [*message["content"], text_block(f"<шоу сейчас>\n{state}\n</шоу сейчас>")],
        }

"""The provider-neutral LLM interface the assistant talks to (ADR-034).

A conversation is a list of messages ``{"role": "user" | "assistant", "content": [Block, ...]}``.
Blocks are plain dicts, so they go to SQLite and over the API as they are:

- ``{"type": "text", "text": ...}``
- ``{"type": "tool_use", "id": ..., "name": ..., "input": {...}}`` (assistant messages)
- ``{"type": "tool_result", "tool_use_id": ..., "content": "...", "is_error": bool}``
  (user messages)

A provider turns this into its own wire format; nothing outside the provider sees that format.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict, TypeVar

from pydantic import BaseModel

Block = dict[str, Any]
Role = Literal["user", "assistant"]
StopReason = Literal["end_turn", "tool_use", "max_tokens"]
OnText = Callable[[str], None]
ModelT = TypeVar("ModelT", bound=BaseModel)


class Message(TypedDict):
    role: Role
    content: list[Block]


class LLMError(Exception):
    """The model could not answer; the message is in Russian and can be shown to the user."""


def text_block(text: str) -> Block:
    return {"type": "text", "text": text}


def tool_use_block(id: str, name: str, input: Mapping[str, Any]) -> Block:
    return {"type": "tool_use", "id": id, "name": name, "input": dict(input)}


def tool_result_block(tool_use_id: str, content: str, is_error: bool = False) -> Block:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call; ``schema`` is the JSON schema of its input object."""

    name: str
    description: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class AssistantMessage:
    """One model reply: text and tool_use blocks, and why the model stopped."""

    content: list[Block]
    stop: StopReason = "end_turn"
    usage: Usage = field(default_factory=Usage)

    @property
    def text(self) -> str:
        return "".join(b["text"] for b in self.content if b["type"] == "text")

    @property
    def tool_uses(self) -> list[Block]:
        return [b for b in self.content if b["type"] == "tool_use"]

    def to_message(self) -> Message:
        return {"role": "assistant", "content": copy.deepcopy(self.content)}


class LLMClient(Protocol):
    """What the agent needs from a model. Both calls block; both raise ``LLMError``.

    Exceptions raised by ``on_text`` (a cancelled turn) propagate unchanged.
    """

    def respond(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        on_text: OnText | None = None,
    ) -> AssistantMessage:
        """The next assistant message; text is streamed to ``on_text`` as it arrives."""
        ...

    def structured(self, system: str, messages: Sequence[Message], model: type[ModelT]) -> ModelT:
        """An answer that fits ``model``'s schema, validated."""
        ...


@dataclass(frozen=True)
class ScriptedRequest:
    kind: Literal["respond", "structured"]
    system: str
    messages: list[Message]
    tools: list[ToolSpec] = field(default_factory=list)
    model: type[BaseModel] | None = None


Reply = AssistantMessage | BaseModel | Mapping[str, Any] | Exception


class ScriptedClient:
    """A stand-in model for tests: replays prepared replies in order and records every request.

    A reply is an ``AssistantMessage`` (for ``respond``), a model instance or a dict validated
    against the requested model (for ``structured``), or an exception to raise.
    """

    def __init__(self, replies: Iterable[Reply]) -> None:
        self.replies = list(replies)
        self.requests: list[ScriptedRequest] = []

    def _next(self) -> Reply:
        if not self.replies:
            raise RuntimeError("ScriptedClient has no replies left")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def respond(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        on_text: OnText | None = None,
    ) -> AssistantMessage:
        self.requests.append(
            ScriptedRequest("respond", system, copy.deepcopy(list(messages)), list(tools))
        )
        reply = self._next()
        if not isinstance(reply, AssistantMessage):
            raise TypeError(f"respond() got a scripted {type(reply).__name__}")
        if on_text is not None and reply.text:
            on_text(reply.text)
        return reply

    def structured(self, system: str, messages: Sequence[Message], model: type[ModelT]) -> ModelT:
        self.requests.append(
            ScriptedRequest("structured", system, copy.deepcopy(list(messages)), model=model)
        )
        reply = self._next()
        if isinstance(reply, AssistantMessage):
            raise TypeError("structured() got a scripted AssistantMessage")
        if isinstance(reply, model):
            return reply
        data = reply.model_dump() if isinstance(reply, BaseModel) else reply
        return model.model_validate(data)

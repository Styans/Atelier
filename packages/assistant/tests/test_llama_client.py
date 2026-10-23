"""``LlamaServerClient`` against canned llama-server responses (``httpx.MockTransport``)."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, Field

from atelier_assistant import (
    LlamaServer,
    LlamaServerClient,
    LlamaServerSettings,
    LLMError,
    Message,
    ToolSpec,
    text_block,
    tool_result_block,
    tool_use_block,
)
from atelier_assistant.llama_server import SCHEMA_HINT, strict_schema, to_openai_messages

Handler = Callable[[httpx.Request], httpx.Response]
TOOLS = [
    ToolSpec(
        "update_brief",
        "Change the brief",
        {"type": "object", "properties": {"duration_s": {"type": "integer"}}},
    )
]


class StubServer(LlamaServer):
    """A server that is always up, so the client can be tested without a process."""

    def __init__(self, tmp: Path) -> None:
        super().__init__(LlamaServerSettings(exe=tmp / "x.exe", model=tmp / "m.gguf"), tmp / "log")
        self.starts = 0

    def start(self) -> None:
        self.starts += 1

    @property
    def url(self) -> str:
        return "http://llama.test"


def sse(*chunks: dict[str, Any]) -> httpx.Response:
    body = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks)
    return httpx.Response(200, text=body + "data: [DONE]\n\n")


def delta(finish: str | None = None, **d: Any) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": d, "finish_reason": finish}]}


def call(index: int, arguments: str, id: str = "", name: str = "") -> dict[str, Any]:
    fn: dict[str, Any] = {"arguments": arguments}
    if name:
        fn["name"] = name
    tc: dict[str, Any] = {"index": index, "function": fn}
    if id:
        tc["id"] = id
        tc["type"] = "function"
    return tc


def ask(text: str) -> list[Message]:
    return [{"role": "user", "content": [text_block(text)]}]


@pytest.fixture
def requests() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def make_client(
    tmp_path: Path, requests: list[dict[str, Any]]
) -> Callable[[Handler], LlamaServerClient]:
    def make(handler: Handler) -> LlamaServerClient:
        def record(request: httpx.Request) -> httpx.Response:
            assert request.url == "http://llama.test/v1/chat/completions"
            requests.append(json.loads(request.content))
            return handler(request)

        return LlamaServerClient(StubServer(tmp_path), transport=httpx.MockTransport(record))

    return make


def test_streams_text(
    make_client: Callable[[Handler], LlamaServerClient], requests: list[dict[str, Any]]
) -> None:
    client = make_client(
        lambda _: sse(
            delta(role="assistant", content=""),
            delta(content="Шоу "),
            delta(content="на 4 минуты."),
            delta("stop"),
            {"choices": [], "usage": {"prompt_tokens": 812, "completion_tokens": 9}},
        )
    )
    streamed: list[str] = []

    reply = client.respond("Ты ассистент.", ask("привет"), TOOLS, streamed.append)

    assert streamed == ["Шоу ", "на 4 минуты."]
    assert reply.content == [text_block("Шоу на 4 минуты.")]
    assert reply.stop == "end_turn"
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (812, 9)
    body = requests[0]
    assert body["stream"] is True
    assert body["temperature"] == 0.4 and body["max_tokens"] == 2048
    assert body["messages"] == [
        {"role": "system", "content": "Ты ассистент."},
        {"role": "user", "content": "привет"},
    ]
    assert body["tools"][0] == {
        "type": "function",
        "function": {
            "name": "update_brief",
            "description": "Change the brief",
            "parameters": TOOLS[0].schema,
        },
    }
    assert isinstance(client.server, StubServer) and client.server.starts == 1


def test_collects_tool_calls_split_across_chunks(
    make_client: Callable[[Handler], LlamaServerClient], requests: list[dict[str, Any]]
) -> None:
    client = make_client(
        lambda _: sse(
            delta(tool_calls=[call(0, "", id="c1", name="update_brief")]),
            delta(tool_calls=[call(0, '{"durat')]),
            delta(tool_calls=[call(0, 'ion_s": 240}')]),
            delta(tool_calls=[call(1, '{"duration_s": 300}', id="c2", name="update_brief")]),
            delta("tool_calls"),
        )
    )

    reply = client.respond("s", ask("4 минуты"), TOOLS)

    assert reply.stop == "tool_use"
    assert reply.content == [
        tool_use_block("c1", "update_brief", {"duration_s": 240}),
        tool_use_block("c2", "update_brief", {"duration_s": 300}),
    ]


def test_keeps_invalid_arguments_and_names_missing_ids(
    make_client: Callable[[Handler], LlamaServerClient],
) -> None:
    client = make_client(
        lambda _: sse(
            delta(content="Сейчас."),
            delta(tool_calls=[call(0, '{"duration_s": 24', name="update_brief")]),
            delta(tool_calls=[call(1, "[1, 2]", name="update_brief")]),
            delta("stop"),  # some templates report stop even with tool calls
        )
    )

    reply = client.respond("s", ask("x"), TOOLS)

    assert reply.stop == "tool_use"
    first, second = reply.tool_uses
    assert first["input"] == {"__invalid_json__": '{"duration_s": 24'}
    assert second["input"] == {"__invalid_json__": "[1, 2]"}
    assert first["id"].startswith("call_") and first["id"] != second["id"]


def test_length_stop_is_max_tokens(make_client: Callable[[Handler], LlamaServerClient]) -> None:
    client = make_client(lambda _: sse(delta(content="Очень длинный"), delta("length")))

    reply = client.respond("s", ask("x"), [])

    assert reply.stop == "max_tokens"
    assert reply.text == "Очень длинный"


def test_errors_are_readable(make_client: Callable[[Handler], LlamaServerClient]) -> None:
    too_long = {"error": {"code": 400, "message": "the request exceeds the available context"}}
    client = make_client(lambda _: httpx.Response(400, json=too_long))
    with pytest.raises(LLMError, match="ошибку 400: the request exceeds the available context"):
        client.respond("s", ask("x"), [])

    client = make_client(lambda _: sse(delta(content="a"), {"error": {"message": "slot gone"}}))
    with pytest.raises(LLMError, match="slot gone"):
        client.respond("s", ask("x"), [])

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(refuse)
    with pytest.raises(LLMError, match="не отвечает"):
        client.respond("s", ask("x"), [])
    with pytest.raises(LLMError, match="не отвечает"):
        client.structured("s", ask("x"), Brief)


def test_exceptions_from_on_text_propagate(
    make_client: Callable[[Handler], LlamaServerClient],
) -> None:
    class Stop(Exception):
        pass

    def cancel(_: str) -> None:
        raise Stop

    client = make_client(lambda _: sse(delta(content="a"), delta(content="b")))
    with pytest.raises(Stop):
        client.respond("s", ask("x"), [], cancel)


def test_history_becomes_openai_messages() -> None:
    history: list[Message] = [
        {"role": "user", "content": [text_block("сделай 4 минуты")]},
        {
            "role": "assistant",
            "content": [
                text_block("Меняю."),
                tool_use_block("c1", "update_brief", {"duration_s": 240, "note": "юбилей"}),
                tool_use_block("c2", "get_timeline", {}),
            ],
        },
        {
            "role": "user",
            "content": [
                tool_result_block("c1", "duration_s: 600 → 240"),
                tool_result_block("c2", "нет программы", is_error=True),
                text_block("и тёплые цвета"),
            ],
        },
        {"role": "assistant", "content": [tool_use_block("c3", "x", {"__invalid_json__": "{"})]},
        {"role": "user", "content": [tool_result_block("c3", "")]},
    ]

    messages = to_openai_messages("sys", history)

    assert messages == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "сделай 4 минуты"},
        {
            "role": "assistant",
            "content": "Меняю.",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "update_brief",
                        "arguments": '{"duration_s": 240, "note": "юбилей"}',
                    },
                },
                {
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "get_timeline", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "duration_s: 600 → 240"},
        {"role": "tool", "tool_call_id": "c2", "content": "ОШИБКА: нет программы"},
        {"role": "user", "content": "и тёплые цвета"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c3",
                    "type": "function",
                    "function": {"name": "x", "arguments": '{"__invalid_json__": "{"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c3", "content": "(пусто)"},
    ]


# --- structured ------------------------------------------------------------------------------


class Scene(BaseModel):
    name: str = Field(max_length=40)
    hold_s: float = Field(ge=6, le=40)


class Brief(BaseModel):
    title: str
    drones: int = Field(description="Число дронов")
    scenes: list[Scene] = Field(max_length=12)


GOOD = {"title": "Юбилей", "drones": 150, "scenes": [{"name": "Торт", "hold_s": 12}]}


def completion(content: str, finish: str = "stop") -> httpx.Response:
    choice = {"index": 0, "message": {"role": "assistant", "content": content}}
    return httpx.Response(200, json={"choices": [{**choice, "finish_reason": finish}]})


def test_structured_answer_by_schema(
    make_client: Callable[[Handler], LlamaServerClient], requests: list[dict[str, Any]]
) -> None:
    client = make_client(lambda _: completion(json.dumps(GOOD, ensure_ascii=False)))

    brief = client.structured("s", ask("шоу на юбилей"), Brief)

    assert brief == Brief.model_validate(GOOD)
    body = requests[0]
    assert "stream" not in body and "tools" not in body
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "Brief" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == strict_schema(Brief)
    system = body["messages"][0]["content"]
    assert system.startswith(f"s\n\n{SCHEMA_HINT}\n{{")
    assert '"description":"Число дронов"' in system  # the model sees what the fields mean


def test_structured_retries_once_livelier(
    make_client: Callable[[Handler], LlamaServerClient], requests: list[dict[str, Any]]
) -> None:
    answers = iter(
        [completion('{"title": "Юбилей", "drones": "много"'), completion(json.dumps(GOOD))]
    )
    client = make_client(lambda _: next(answers))

    assert client.structured("s", ask("x"), Brief).drones == 150
    assert [r["temperature"] for r in requests] == [0.4, 0.8]
    assert "repeat_penalty" not in requests[0] and requests[1]["repeat_penalty"] == 1.25


def test_structured_gives_up_after_the_retry(
    make_client: Callable[[Handler], LlamaServerClient], requests: list[dict[str, Any]]
) -> None:
    answers = iter([completion('{"title": "Юбилей Юбилей Юбилей', "length"), completion("{}")])
    client = make_client(lambda _: next(answers))

    with pytest.raises(LLMError, match="не по схеме Brief: title: Field required"):
        client.structured("s", ask("x"), Brief)
    assert len(requests) == 2


def test_strict_schema_closes_objects_and_keeps_limits() -> None:
    schema = strict_schema(Brief)

    assert schema["additionalProperties"] is False
    assert "title" not in schema
    scene = schema["$defs"]["Scene"]
    assert scene["additionalProperties"] is False
    assert scene["properties"]["name"]["maxLength"] == 40
    assert scene["properties"]["hold_s"]["minimum"] == 6
    assert schema["properties"]["scenes"]["maxItems"] == 12
    assert schema["properties"]["title"] == {"type": "string"}  # a field named "title" stays

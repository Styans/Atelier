"""The real llama-server on the GPU: one tool call and one structured answer.

Runs only with ``uv run pytest -m llm`` and when ATELIER_LLAMA_EXE and ATELIER_LLAMA_MODEL point
at the files (e.g. Pygmalion's ``runtime\\models\\llamacpp\\llama-server.exe`` and
``runtime\\models\\llm\\qwen3-vl-8b\\Qwen3VL-8B-Instruct-Q4_K_M.gguf``). Takes ~6 GB of video
memory: do not run it next to a GPU job.
"""

import os
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel, Field

from atelier_assistant import (
    LlamaServer,
    LlamaServerClient,
    LlamaServerSettings,
    Message,
    ToolSpec,
    text_block,
)

EXE = Path(os.environ.get("ATELIER_LLAMA_EXE", ""))
MODEL = Path(os.environ.get("ATELIER_LLAMA_MODEL", ""))

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not (EXE.is_file() and MODEL.is_file()),
        reason="ATELIER_LLAMA_EXE / ATELIER_LLAMA_MODEL do not point at llama-server and weights",
    ),
]

SET_DURATION = ToolSpec(
    "set_duration",
    "Set the show duration in the brief.",
    {
        "type": "object",
        "properties": {"duration_s": {"type": "integer", "description": "Seconds"}},
        "required": ["duration_s"],
    },
)


class Brief(BaseModel):
    occasion: str = Field(max_length=80)
    drones: int
    duration_s: int = Field(description="Длительность шоу в секундах")
    warm_colors: bool


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LlamaServerClient]:
    log = tmp_path_factory.mktemp("llama") / "llama-server.log"
    server = LlamaServer(LlamaServerSettings(exe=EXE, model=MODEL), log)
    client = LlamaServerClient(server)
    try:
        server.start()
        print(f"\nllama-server ready in {server.last_startup_s:.1f} s")
        yield client
    finally:
        client.close()
        server.stop()
    assert not server.running()


def ask(text: str) -> list[Message]:
    return [{"role": "user", "content": [text_block(text)]}]


def test_tool_call(client: LlamaServerClient) -> None:
    streamed: list[str] = []
    started = time.monotonic()

    reply = client.respond(
        "Ты помощник автора дрон-шоу. Меняй бриф только через инструменты.",
        ask("Сделай шоу длительностью 4 минуты."),
        [SET_DURATION],
        streamed.append,
    )

    elapsed = time.monotonic() - started
    print(f"tool call in {elapsed:.1f} s, usage {reply.usage}")
    assert reply.stop == "tool_use"
    (call,) = reply.tool_uses
    assert call["name"] == "set_duration"
    assert call["input"] == {"duration_s": 240}


def test_structured_answer(client: LlamaServerClient) -> None:
    started = time.monotonic()

    brief = client.structured(
        "Извлеки бриф дрон-шоу из сообщения пользователя.",
        ask("шоу на 35-летие компании, 150 дронов, 4 минуты, зритель на юге, тёплые цвета"),
        Brief,
    )

    print(f"structured in {time.monotonic() - started:.1f} s: {brief}")
    assert brief.drones == 150
    assert brief.duration_s == 240
    assert brief.warm_colors


def test_generation_speed(client: LlamaServerClient) -> None:
    """Not a check, a measurement: llama-server reports its own timings."""
    response = httpx.post(
        client.server.url + "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "Опиши дрон-шоу в 150 словах."}],
            "max_tokens": 256,
        },
        timeout=300,
    )
    timings = response.json()["timings"]
    print(
        f"prompt {timings['prompt_per_token_ms']:.1f} ms/token, "
        f"generation {timings['predicted_per_second']:.1f} tokens/s"
    )
    assert timings["predicted_per_second"] > 0

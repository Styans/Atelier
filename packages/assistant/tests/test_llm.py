import pytest
from pydantic import BaseModel, ValidationError

from atelier_assistant import (
    AssistantMessage,
    LLMClient,
    LLMError,
    Message,
    ScriptedClient,
    ToolSpec,
    text_block,
    tool_result_block,
    tool_use_block,
)


class Brief(BaseModel):
    title: str
    drones: int


TOOLS = [ToolSpec("update_brief", "Change the brief", {"type": "object", "properties": {}})]


def ask(text: str) -> list[Message]:
    return [{"role": "user", "content": [text_block(text)]}]


def test_assistant_message_splits_text_and_tool_uses() -> None:
    reply = AssistantMessage(
        [text_block("Меняю "), text_block("бриф"), tool_use_block("c1", "update_brief", {"a": 1})],
        stop="tool_use",
    )

    assert reply.text == "Меняю бриф"
    assert [b["id"] for b in reply.tool_uses] == ["c1"]
    message = reply.to_message()
    assert message["role"] == "assistant"
    message["content"][2]["input"]["a"] = 2
    assert reply.content[2]["input"] == {"a": 1}  # a copy goes into the history


def test_tool_result_block_shape() -> None:
    assert tool_result_block("c1", "нет такой сцены", is_error=True) == {
        "type": "tool_result",
        "tool_use_id": "c1",
        "content": "нет такой сцены",
        "is_error": True,
    }


def test_scripted_client_replays_and_records() -> None:
    client = ScriptedClient(
        [
            AssistantMessage([text_block("Привет")]),
            {"title": "Юбилей", "drones": 150},
        ]
    )
    llm: LLMClient = client
    streamed: list[str] = []
    history = ask("шоу на юбилей")

    reply = llm.respond("system", history, TOOLS, streamed.append)
    history.append(reply.to_message())
    brief = llm.structured("system", history, Brief)

    assert streamed == ["Привет"]
    assert brief == Brief(title="Юбилей", drones=150)
    assert [r.kind for r in client.requests] == ["respond", "structured"]
    assert client.requests[0].tools == TOOLS
    assert len(client.requests[0].messages) == 1  # recorded as it was at the call
    assert client.requests[1].model is Brief


def test_scripted_client_raises_scripted_errors_and_validates() -> None:
    client = ScriptedClient([LLMError("модель занята"), {"title": "x"}])

    with pytest.raises(LLMError, match="модель занята"):
        client.respond("s", ask("a"), [])
    with pytest.raises(ValidationError):
        client.structured("s", ask("a"), Brief)
    with pytest.raises(RuntimeError, match="no replies left"):
        client.respond("s", ask("a"), [])


def test_scripted_client_rejects_a_reply_of_the_wrong_kind() -> None:
    client = ScriptedClient([Brief(title="x", drones=1), AssistantMessage([])])

    with pytest.raises(TypeError):
        client.respond("s", ask("a"), [])
    with pytest.raises(TypeError):
        client.structured("s", ask("a"), Brief)

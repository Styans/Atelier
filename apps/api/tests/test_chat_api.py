import json
from typing import Any

from fastapi.testclient import TestClient

from atelier_assistant.llm import (
    AssistantMessage,
    LLMError,
    ScriptedClient,
    text_block,
    tool_use_block,
)
from atelier_jobs import ChatStore, JobQueue
from atelier_show_engine.program import FigureDraft, SceneDraft
from atelier_worker.worker import Worker

from .test_api import new_show, ok

Env = tuple[TestClient, Worker]


def parts(client: TestClient) -> tuple[ChatStore, JobQueue]:
    app: Any = client.app
    return app.state.chat, app.state.queue


def drafts(n: int) -> list[dict[str, Any]]:
    return [
        SceneDraft(title=f"Сцена {i}", hold_s=10, figure=FigureDraft(name=f"F{i}")).model_dump(
            mode="json"
        )
        for i in range(n)
    ]


def test_a_message_starts_one_urgent_turn(env: Env) -> None:
    client, _ = env
    sid = new_show(client)["id"]

    posted = ok(client.post(f"/api/shows/{sid}/chat", json={"text": "шоу на юбилей"}), 202)
    again = client.post(f"/api/shows/{sid}/chat", json={"text": "ещё"})
    history = ok(client.get(f"/api/shows/{sid}/chat"))

    assert posted["job"]["kind"] == "assistant_turn" and posted["job"]["priority"] == 10
    assert again.status_code == 409
    assert [m["role"] for m in history["messages"]] == ["user"]
    assert history["messages"][0]["content"][0]["text"] == "шоу на юбилей"
    assert history["active_turn"]["id"] == posted["job"]["id"]


def test_the_turn_is_streamed_until_it_is_done(env: Env) -> None:
    client, _ = env
    chat, queue = parts(client)
    sid = new_show(client)["id"]
    turn = ok(client.post(f"/api/shows/{sid}/chat", json={"text": "привет"}), 202)["job"]["id"]
    chat.emit(turn, "status", {"stage": "thinking"})
    chat.emit(turn, "text", {"delta": "Здравствуйте"})
    chat.emit(turn, "done", {"revision_before": 1, "revision_after": 1})

    with client.stream("GET", f"/api/shows/{sid}/chat/events?turn={turn}") as response:
        body = "".join(response.iter_text())

    kinds = [line.split(": ", 1)[1] for line in body.splitlines() if line.startswith("event: ")]
    assert kinds == ["status", "text", "done"]
    assert "Здравствуйте" in body

    queue.claim(pid=1)
    queue.finish(turn, "done")
    with client.stream("GET", f"/api/shows/{sid}/chat/events?turn={turn}&after=99") as response:
        ended = "".join(response.iter_text())
    assert "event: end" in ended and json.dumps({"status": "done"}) in ended


def test_an_answer_is_rolled_back_to_where_its_turn_started(env: Env) -> None:
    client, _ = env
    chat, _ = parts(client)
    show = new_show(client)
    sid, before = show["id"], show["revision"]
    turn = ok(client.post(f"/api/shows/{sid}/chat", json={"text": "программа"}), 202)["job"]["id"]
    app: Any = client.app
    service = app.state.service
    store = service.open(sid)
    service.apply_program(store, [SceneDraft.model_validate(d) for d in drafts(6)])
    chat.append(
        sid, "assistant", [{"type": "text", "text": "готово"}], turn, revision_before=before
    )

    view = ok(client.post(f"/api/shows/{sid}/chat/{turn}/rollback", json={}))

    assert view["show"]["program"] == [] and view["show"]["revision"] == before + 2
    assert client.post(f"/api/shows/{sid}/chat/j_nope/rollback", json={}).status_code == 404


def test_a_whole_turn_through_the_queue(env: Env, scripted: ScriptedClient) -> None:
    client, worker = env
    sid = new_show(client, drones=16)["id"]
    scenes = [{"title": f"Сцена {i}", "hold_s": 12, "figure": {"name": f"F{i}"}} for i in range(6)]
    scripted.replies = [
        AssistantMessage(
            [
                tool_use_block("c1", "update_brief", {"occasion": "юбилей", "duration_s": 150}),
                tool_use_block("c2", "propose_program", {"scenes": scenes}),
            ],
            stop="tool_use",
        ),
        AssistantMessage([text_block("Сделал бриф и программу из 6 сцен.")]),
    ]
    turn = ok(client.post(f"/api/shows/{sid}/chat", json={"text": "юбилей, 2.5 минуты"}), 202)

    done = worker.run_once()
    with client.stream("GET", f"/api/shows/{sid}/chat/events?turn={turn['job']['id']}") as stream:
        body = "".join(stream.iter_text())
    history = ok(client.get(f"/api/shows/{sid}/chat"))
    show = ok(client.get(f"/api/shows/{sid}"))["show"]

    assert done is not None and done.status == "done", done and done.error
    assert '"stage": "loading"' in body  # the model was not running: the user sees it start
    assert "event: done" in body and "Сделал бриф" in body
    assert [m["role"] for m in history["messages"]] == ["user", "assistant", "user", "assistant"]
    assert history["active_turn"] is None
    assert show["brief"]["occasion"] == "юбилей" and len(show["program"]) == 6
    assert ok(client.get(f"/api/shows/{sid}/timeline"))["total_s"] <= 150


def test_a_turn_without_a_model_says_so_in_the_chat(env: Env, scripted: ScriptedClient) -> None:
    client, worker = env
    sid = new_show(client)["id"]
    scripted.replies = [LLMError("Локальная модель не отвечает")]
    ok(client.post(f"/api/shows/{sid}/chat", json={"text": "привет"}), 202)

    done = worker.run_once()
    messages = ok(client.get(f"/api/shows/{sid}/chat"))["messages"]

    assert done is not None and done.status == "failed"
    assert messages[-1]["role"] == "notice"
    assert "не отвечает" in messages[-1]["content"][0]["text"]


def test_assistant_health_explains_what_is_missing(env: Env) -> None:
    client, _ = env
    health = ok(client.get("/api/assistant/health"))

    assert health["configured"] is False and "[assistant.llama_server]" in health["problem"]


def test_proposals_run_only_when_applied_and_only_once(env: Env) -> None:
    client, _ = env
    chat, _ = parts(client)
    sid = new_show(client)["id"]
    replace = chat.add_proposal(sid, "replace_program", {"scenes": drafts(6)}, "новая программа")
    compile_ = chat.add_proposal(sid, "compile", {}, "скомпилировать")
    dismissed = chat.add_proposal(sid, "export", {}, "экспорт")

    applied = ok(client.post(f"/api/shows/{sid}/proposals/{replace.id}/apply", json={}))
    queued = ok(client.post(f"/api/shows/{sid}/proposals/{compile_.id}/apply", json={}))
    ok(client.post(f"/api/shows/{sid}/proposals/{dismissed.id}/dismiss", json={}))

    assert len(applied["view"]["show"]["program"]) == 6 and applied["job"] is None
    assert queued["job"]["kind"] == "compile" and queued["proposal"]["status"] == "applied"
    twice = client.post(f"/api/shows/{sid}/proposals/{replace.id}/apply", json={})
    assert twice.status_code == 409
    statuses = {p["id"]: p["status"] for p in ok(client.get(f"/api/shows/{sid}/chat"))["proposals"]}
    assert statuses == {replace.id: "applied", compile_.id: "applied", dismissed.id: "dismissed"}

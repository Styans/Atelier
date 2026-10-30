from pathlib import Path

import pytest

from atelier_jobs import ChatStore, JobQueue


@pytest.fixture
def chat(tmp_path: Path) -> ChatStore:
    return ChatStore(JobQueue(tmp_path / "atelier.db"))


def _text(value: str) -> list[dict[str, object]]:
    return [{"type": "text", "text": value}]


def test_history_is_numbered_per_show(chat: ChatStore) -> None:
    chat.append("a", "user", _text("привет"))
    chat.append("b", "user", _text("other show"))
    answer = chat.append(
        "a",
        "assistant",
        [
            {"type": "tool_use", "id": "t1", "name": "set_brief", "input": {"duration_s": 300}},
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": False},
            {"type": "text", "text": "готово"},
        ],
        turn_job_id="j_1",
        revision_before=3,
    )
    chat.append("a", "notice", _text("rolled back"))
    chat.set_revision_after(answer.id or 0, 5)

    history = chat.history("a")
    assert [(m.seq, m.role) for m in history] == [(1, "user"), (2, "assistant"), (3, "notice")]
    assert [m.seq for m in chat.history("b")] == [1]
    assert history[0].blocks() == _text("привет")
    assert history[1].public()["content"][0]["input"] == {"duration_s": 300}
    assert (history[1].turn_job_id, history[1].revision_before) == ("j_1", 3)
    assert history[1].revision_after == 5
    assert [m.seq for m in chat.history("a", limit=2)] == [2, 3]


def test_a_rolled_back_turn_is_marked_once(chat: ChatStore) -> None:
    chat.append("a", "user", _text("первый"), turn_job_id="j_1")
    chat.append("a", "user", _text("второй"), turn_job_id="j_2")
    chat.append("a", "assistant", _text("ок"), turn_job_id="j_2")

    assert chat.mark_rolled_back("a", "j_2") == 2
    assert chat.mark_rolled_back("a", "j_2") == 0
    assert [m.rolled_back_at is not None for m in chat.history("a")] == [False, True, True]


def test_bad_role_or_block_is_refused(chat: ChatStore) -> None:
    with pytest.raises(ValueError, match="unknown chat role"):
        chat.append("a", "system", _text("x"))
    with pytest.raises(ValueError, match="unknown content block"):
        chat.append("a", "user", [{"type": "image"}])
    assert chat.history("a") == []


def test_events_page_by_id_cursor(chat: ChatStore) -> None:
    for index in range(5):
        chat.emit("j_1", "text", {"delta": str(index)})
        chat.emit("j_2", "status", {"index": index})
    chat.emit("j_1", "done", {})

    first = chat.events_since("j_1", 0, limit=4)
    rest = chat.events_since("j_1", first[-1].id or 0)
    assert [e.seq for e in first + rest] == [1, 2, 3, 4, 5, 6]
    assert [e.public()["payload"] for e in first] == [{"delta": str(i)} for i in range(4)]
    assert [e.kind for e in rest] == ["text", "done"]
    assert chat.events_since("j_1", rest[-1].id or 0) == []
    assert [e.seq for e in chat.events_since("j_2")] == [1, 2, 3, 4, 5]
    with pytest.raises(ValueError, match="unknown chat event"):
        chat.emit("j_1", "thinking", {})


def test_proposal_is_decided_once(chat: ChatStore) -> None:
    applied = chat.add_proposal("a", "compile", {}, "Собрать шоу", message_id=2)
    dismissed = chat.add_proposal("a", "generate_images", {"scene_id": "s1"}, "Картинки")
    chat.add_proposal("b", "export")

    assert [p.id for p in chat.proposals("a")] == [applied.id, dismissed.id]
    assert chat.decide_proposal(applied.id, "applied").status == "applied"
    decided = chat.decide_proposal(dismissed.id, "dismissed")
    assert decided.decided_at is not None and decided.public()["args"] == {"scene_id": "s1"}
    assert chat.proposals("a", status="pending") == []
    assert [p.id for p in chat.proposals("a", status="applied")] == [applied.id]

    with pytest.raises(ValueError, match="already applied"):
        chat.decide_proposal(applied.id, "dismissed")
    with pytest.raises(ValueError, match="already dismissed"):
        chat.decide_proposal(dismissed.id, "applied")
    with pytest.raises(ValueError, match="only be applied or dismissed"):
        chat.decide_proposal(chat.proposals("b")[0].id, "pending")
    with pytest.raises(KeyError):
        chat.decide_proposal("p_missing", "applied")


def test_store_shares_the_queue_database(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "atelier.db")
    ChatStore(queue).append("a", "user", _text("x"))

    assert [m.seq for m in ChatStore(queue.engine).history("a")] == [1]

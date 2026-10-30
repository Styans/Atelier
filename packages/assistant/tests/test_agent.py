from typing import Any

import pytest

from atelier_assistant.agent import HISTORY_CHARS, MAX_STEPS, Agent, to_messages
from atelier_assistant.llm import AssistantMessage, ScriptedClient, text_block, tool_use_block
from atelier_jobs import ChatStore
from atelier_show_engine.service import ShowService
from atelier_show_engine.store import ShowStore

PHRASE = "Шоу на 35-летие компании, 150 дронов, 4 минуты, зритель с юга, тёплые цвета"


def figure(i: int) -> dict[str, Any]:
    return {
        "name": f"Фигура {i}",
        "description": "простой силуэт",
        "image_prompt": "a simple flat silhouette, white background",
    }


def program(n: int, hold: float = 15) -> dict[str, Any]:
    return {
        "scenes": [
            {"title": f"Сцена {i}", "hold_s": hold, "figure": figure(i)} for i in range(1, n + 1)
        ]
    }


def calls(*items: tuple[str, dict[str, Any]]) -> AssistantMessage:
    return AssistantMessage(
        [tool_use_block(f"call_{i}", name, args) for i, (name, args) in enumerate(items)],
        stop="tool_use",
    )


def answer(text: str) -> AssistantMessage:
    return AssistantMessage([text_block(text)])


class Turn:
    def __init__(self, service: ShowService, chat: ChatStore, store: ShowStore) -> None:
        self.service, self.chat, self.store = service, chat, store
        self.events: list[tuple[str, dict[str, Any]]] = []

    def run(self, message: str, replies: list[Any]) -> tuple[Any, ScriptedClient]:
        show_id = self.store.load().id
        self.chat.append(show_id, "user", [text_block(message)], turn_job_id="j_1")
        llm = ScriptedClient(replies)
        result = Agent(llm, self.service, self.chat).run_turn(
            self.store, show_id, "j_1", lambda kind, data: self.events.append((kind, data))
        )
        return result, llm


@pytest.fixture
def turn(service: ShowService, chat: ChatStore) -> Turn:
    store, _ = service.create_show("Новое шоу", drones=150, duration_s=600)
    return Turn(service, chat, store)


def test_one_phrase_gives_a_brief_and_a_fitted_program(turn: Turn) -> None:
    brief = {
        "occasion": "35-летие компании",
        "duration_s": 240,
        "audience_side": "север",
        "palette": ["#ffb300", "#ff6f00"],
        "mood": "тёплое, праздничное",
    }
    result, llm = turn.run(
        PHRASE,
        [calls(("update_brief", brief), ("propose_program", program(8))), answer("Готово.")],
    )

    show = turn.store.load()
    assert show.brief.occasion == "35-летие компании" and show.brief.duration_s == 240
    assert show.brief.viewer_direction == "+Y"  # "север" mapped by the code, not the model
    assert len(show.program) == 8 and len(show.models) == 8
    assert all(card.image_prompt for card in show.models.values())
    assert turn.service.estimate_timeline(turn.store).total_s <= 240
    assert result.revision_after > result.revision_before and result.stopped is None
    assert {r.author for r in turn.store.history()[1:]} == {"assistant"}

    second = llm.requests[1].messages
    results = second[-1]["content"]
    assert [b["type"] for b in results[:2]] == ["tool_result", "tool_result"]
    assert "оценка шоу" in results[1]["content"] and not results[1]["is_error"]
    assert (
        results[-1]["text"].startswith("<шоу сейчас>")
        and "Программа (8 сцен)" in results[-1]["text"]
    )
    kinds = [kind for kind, _ in turn.events]
    assert (
        kinds[0] == "status" and kinds[-1] == "done" and "tool_result" in kinds and "text" in kinds
    )
    roles = [m.role for m in turn.chat.history(show.id)]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_an_invalid_call_is_fixed_by_the_model(turn: Turn) -> None:
    result, llm = turn.run(
        "длительность 3 минуты",
        [
            calls(("update_brief", {"duration_s": "три минуты"})),
            calls(("update_brief", {"duration_s": 180})),
            answer("Поставил 3 минуты."),
        ],
    )

    error = llm.requests[1].messages[-1]["content"][0]
    assert error["is_error"] and "duration_s" in error["content"]
    assert turn.store.load().brief.duration_s == 180
    assert result.failures == 1 and result.stopped is None


def test_three_failures_in_a_row_stop_the_turn_honestly(turn: Turn) -> None:
    bad = calls(("update_scene", {"scene_id": "s_9", "hold_s": 10}))
    result, _ = turn.run("удлини девятую сцену", [bad, bad, bad, answer("не дойдёт")])

    assert result.stopped and "s_9" in result.stopped
    last = turn.chat.history(turn.store.load().id)[-1]
    assert last.role == "notice" and "Не получилось" in last.blocks()[0]["text"]
    assert last.revision_after == result.revision_after


def test_a_program_too_long_is_replaced_by_the_model_itself(turn: Turn) -> None:
    turn.service.update_brief(turn.store, {"duration_s": 120})
    result, llm = turn.run(
        "программа на 2 минуты",
        [
            calls(("propose_program", program(12))),
            calls(("propose_program", program(6, hold=8))),
            answer("Сократил до шести сцен."),
        ],
    )

    first = llm.requests[1].messages[-1]["content"][0]["content"]
    assert "не помещается" in first and "propose_program снова" in first
    show = turn.store.load()
    assert len(show.program) == 6 and len(show.models) == 6  # cards of the first try are gone
    assert not result.proposals


def test_the_users_program_is_only_replaced_by_a_click(turn: Turn) -> None:
    # A program from an earlier turn is the user's now: replacing it removes their scenes.
    turn.run("сделай программу", [calls(("propose_program", program(6))), answer("ok")])
    before = turn.store.load()
    result, llm = turn.run(
        "другую программу", [calls(("propose_program", program(7))), answer("ok")]
    )

    assert len(turn.store.load().program) == 6 and turn.store.load().revision == before.revision
    (proposal,) = turn.chat.proposals(before.id)
    assert proposal.action == "replace_program" and len(proposal.public()["args"]["scenes"]) == 7
    assert result.proposals == [proposal.id]
    assert "предложение показано" in llm.requests[1].messages[-1]["content"][0]["content"]


def test_the_same_proposal_is_shown_once(turn: Turn) -> None:
    turn.run("сделай программу", [calls(("propose_program", program(6))), answer("ok")])
    remove = {"action": "remove_scene", "target_id": "s_4", "reason": "лишняя сцена"}
    result, llm = turn.run(
        "убери четвёртую",
        [calls(("propose_action", remove)), calls(("propose_action", remove)), answer("ok")],
    )

    assert len(turn.chat.proposals(turn.store.load().id)) == 1 and len(result.proposals) == 1
    assert "уже ждёт решения" in llm.requests[2].messages[-1]["content"][0]["content"]


def test_expensive_actions_are_proposals(turn: Turn) -> None:
    turn.run("сделай программу", [calls(("propose_program", program(6))), answer("ok")])
    model_id = turn.store.load().program[0].model_id
    action = {
        "action": "generate_images",
        "target_id": model_id,
        "prompt": "a star",
        "reason": "концепты для первой сцены",
    }
    result, _ = turn.run("сгенерируй концепты", [calls(("propose_action", action)), answer("ok")])

    (proposal,) = turn.chat.proposals(turn.store.load().id, status="pending")
    assert result.proposals == [proposal.id]
    assert proposal.action == "generate_images" and proposal.summary == "концепты для первой сцены"
    assert not turn.store.load().models[model_id].assets.concepts  # nothing ran


def test_a_turn_can_be_rolled_back_to_where_it_started(turn: Turn) -> None:
    result, _ = turn.run(PHRASE, [calls(("propose_program", program(6))), answer("ok")])

    turn.store.rollback(result.revision_before, author="ui")

    show = turn.store.load()
    assert show.program == [] and show.models == {}


def test_the_turn_has_a_step_limit(turn: Turn) -> None:
    loop = calls(("update_brief", {"mood": "спокойное"}))
    result, _ = turn.run("ещё", [loop] * MAX_STEPS)

    assert result.steps == MAX_STEPS and result.stopped and "шагов" in result.stopped


def test_history_is_cut_between_turns_never_inside_one(chat: ChatStore) -> None:
    long = "x" * (HISTORY_CHARS // 2)
    chat.append("s", "user", [text_block(long)])
    chat.append("s", "assistant", [text_block(long)])
    chat.append("s", "user", [text_block("второй вопрос")])
    chat.append("s", "assistant", [tool_use_block("c1", "fit_to_budget", {})])
    chat.append(
        "s",
        "user",
        [{"type": "tool_result", "tool_use_id": "c1", "content": long, "is_error": False}],
    )
    chat.append("s", "notice", [text_block("для людей")])

    messages = to_messages(chat.history("s"))

    assert messages[0]["content"][0]["text"] == "второй вопрос"
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]

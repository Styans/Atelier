import pytest

from atelier_show_engine.estimate import Segment, TimelineEstimate
from atelier_show_engine.program import (
    MAX_HOLD_S,
    MIN_HOLD_S,
    FigureDraft,
    SceneDraft,
    fit_holds,
)
from atelier_show_engine.service import ServiceError, ShowService


def timeline(
    budget: float, holds: dict[str, float], transition: float = 10.0, ascent: float | None = None
) -> TimelineEstimate:
    segments, clock = [Segment("takeoff", None, "T", 0.0, 8.0, "exact")], 8.0
    for index, (scene_id, hold) in enumerate(holds.items()):
        move = ascent if index == 0 and ascent is not None else transition
        segments.append(Segment("transition", scene_id, "", clock, move, "proxy"))
        segments.append(Segment("hold", scene_id, scene_id, clock + move, hold, "exact"))
        clock += move + hold
    segments.append(Segment("transition", None, "Return", clock, 40.0, "proxy"))
    segments.append(Segment("landing", None, "L", clock + 40.0, 10.0, "exact"))
    return TimelineEstimate(segments, budget)


def test_holds_are_scaled_to_the_budget_keeping_proportions() -> None:
    wished = {"s_1": 10.0, "s_2": 20.0, "s_3": 10.0}
    estimate = timeline(160, wished)  # fixed: 8 + 3 x 10 + 40 + 10 = 88 s, 72 s for holds

    fit = fit_holds(estimate, wished)

    assert fit.fits and fit.total_s == pytest.approx(160, abs=1.5)
    assert fit.holds == {"s_1": 18.0, "s_2": 36.0, "s_3": 18.0}
    assert fit.total_s <= 160


def test_limits_are_kept_and_the_rest_shared() -> None:
    wished = {"s_1": 1.0, "s_2": 100.0}
    fit = fit_holds(timeline(130, wished), wished)  # 130 - 78 fixed = 52 s for holds

    assert fit.holds["s_1"] >= MIN_HOLD_S and fit.holds["s_2"] <= MAX_HOLD_S
    assert sum(fit.holds.values()) == pytest.approx(52, abs=0.5)


def test_too_many_scenes_for_the_budget_are_counted() -> None:
    wished = {f"s_{i}": 15.0 for i in range(1, 11)}
    fit = fit_holds(timeline(120, wished), wished)  # 10 scenes x (10 + 6) + 58 = 218 s minimum

    assert not fit.fits and fit.excess_s == pytest.approx(218 - 120)
    assert fit.remove_scenes == 7  # 16 s freed per dropped scene: 98 s needs 7 of them
    assert set(fit.holds.values()) == {MIN_HOLD_S}


def test_dropping_a_scene_never_frees_the_climb_to_the_first_one() -> None:
    wished = {f"s_{i}": 15.0 for i in range(1, 7)}
    fit = fit_holds(timeline(150, wished, ascent=43.0), wished)

    # 8 + 43 + 5 x 10 + 6 x 6 + 50 = 187 s, 37 s over; a dropped scene frees 10 + 6 s, not 43
    assert fit.excess_s == pytest.approx(37) and fit.remove_scenes == 3
    assert fit.shortest_s(4) == pytest.approx(187 - 2 * 16)  # what four scenes need at least


def test_a_short_program_stays_short_at_the_maximum() -> None:
    fit = fit_holds(timeline(600, {"s_1": 10.0}), {"s_1": 10.0})

    assert fit.holds == {"s_1": MAX_HOLD_S} and fit.slack_s > 0 and fit.fits


def test_apply_program_creates_cards_and_fits_holds(service: ShowService) -> None:
    store, _ = service.create_show("Юбилей", drones=36, duration_s=150)
    drafts = [
        SceneDraft(
            title=f"Сцена {i}",
            hold_s=12,
            figure=FigureDraft(
                name=f"Фигура {i}", description="силуэт", image_prompt="a gold star"
            ),
        )
        for i in range(1, 7)
    ]
    before = store.current_revision()

    show, fit = service.apply_program(store, drafts, message="Program from the assistant")

    assert store.current_revision() == before + 1  # one revision for the whole program
    assert [s.id for s in show.program] == [f"s_{i}" for i in range(1, 7)]
    assert len(show.models) == 6 and all(not c.assets.mesh for c in show.models.values())
    assert fit.fits and service.estimate_timeline(store).total_s <= 150
    with pytest.raises(ServiceError, match="already has 6 scenes"):
        service.apply_program(store, drafts)

    reused = [SceneDraft(title="Снова", hold_s=10, model_id=show.program[0].model_id)]
    show, _ = service.apply_program(store, reused, replace=True)
    assert len(show.program) == 1 and len(show.models) == 6  # cards stay, only scenes go


def test_a_figure_shown_twice_is_one_card(service: ShowService) -> None:
    store, _ = service.create_show("Юбилей", drones=36, duration_s=300)
    names = ["Логотип", "Звезда", "логотип "]
    drafts = [SceneDraft(title=n, hold_s=10, figure=FigureDraft(name=n)) for n in names]

    show, _ = service.apply_program(store, drafts)

    assert len(show.models) == 2
    assert show.program[0].model_id == show.program[2].model_id


def test_fit_program_follows_a_new_budget(service: ShowService) -> None:
    store, _ = service.create_show("Юбилей", drones=36, duration_s=240)
    drafts = [
        SceneDraft(title=str(i), hold_s=20, figure=FigureDraft(name=f"F{i}")) for i in range(6)
    ]
    service.apply_program(store, drafts)
    service.update_brief(store, {"duration_s": 180})

    _, fit = service.fit_program(store)

    assert fit.fits and service.estimate_timeline(store).total_s <= 180
    assert "Fit holds" in store.history()[-1].message

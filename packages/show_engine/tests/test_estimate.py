from pathlib import Path

import pytest

from atelier_show_engine.estimate import TimelineEstimate, estimate_timeline
from atelier_show_engine.service import ShowService
from atelier_show_engine.store import ShowStore

DRONES = 36


@pytest.fixture
def store(service: ShowService, fixture_files: dict[str, Path]) -> ShowStore:
    store, _ = service.create_show("Оценка", drones=DRONES, duration_s=300)
    service.set_music(store, fixture_files["music"])
    _, torus = service.add_model(store, fixture_files["torus"], name="Torus")
    _, star = service.add_model(store, fixture_files["star"], name="Star")
    service.compute_points(store, torus)
    service.compute_points(store, star)
    service.add_scene(store, torus, hold_s=8)
    service.add_scene(store, star, hold_s=10)
    return store


def pairs(estimate: TimelineEstimate, compiled: TimelineEstimate) -> list[tuple[float, float]]:
    assert [(s.kind, s.scene_id) for s in estimate.segments] == [
        (s.kind, s.scene_id) for s in compiled.segments
    ]
    return [
        (e.duration_s, c.duration_s)
        for e, c in zip(estimate.segments, compiled.segments, strict=True)
    ]


def test_estimate_matches_the_compiler(service: ShowService, store: ShowStore) -> None:
    before = service.estimate_timeline(store)
    service.compile(store)
    after = service.estimate_timeline(store)

    assert not before.compiled and after.compiled
    assert {s.basis for s in before.segments} == {"exact", "points"}
    for estimated, actual in pairs(before, after):
        # Same flights as the default planner, from the same points: identical to the frame.
        assert estimated == pytest.approx(actual, abs=1.5 / 30)
    assert before.total_s == pytest.approx(after.total_s, abs=0.2)
    assert before.music_start_s == pytest.approx(after.music_start_s or 0, abs=0.05)


def test_stand_ins_stay_close_to_the_compiled_show(service: ShowService, store: ShowStore) -> None:
    # What the assistant sees while a program has no meshes yet (ADR-038 calibration; on the
    # 150-drone shows of phase 2 every segment was within 5%).
    proxy = estimate_timeline(store.load(), lambda model_id, n: None)
    service.compile(store)
    compiled = service.estimate_timeline(store)

    for estimated, actual in pairs(proxy, compiled):
        assert estimated == pytest.approx(actual, rel=0.35)
    assert proxy.total_s == pytest.approx(compiled.total_s, rel=0.1)


def test_cards_without_points_get_a_stand_in(service: ShowService, store: ShowStore) -> None:
    with_points = service.estimate_timeline(store)
    _, card = service.create_model(store, "Eagle")
    service.add_scene(store, card, hold_s=12)

    estimate = service.estimate_timeline(store)
    eagle = [s for s in estimate.segments if s.title in ("To Eagle", "Return")]

    assert [s.basis for s in eagle] == ["proxy", "proxy"]
    assert all(s.duration_s >= 2.0 for s in eagle)
    assert estimate.total_s > with_points.total_s + 12


def test_a_fixed_transition_is_taken_as_it_is(service: ShowService, store: ShowStore) -> None:
    scene_id = store.load().program[1].id
    service.update_scene(store, scene_id, {"transition_in": {"duration_s": 3.5}})

    estimate = service.estimate_timeline(store)
    (fixed,) = [s for s in estimate.segments if s.kind == "transition" and s.scene_id == scene_id]

    assert (fixed.duration_s, fixed.basis) == (3.5, "fixed")
    assert estimate.scene_s(scene_id) == pytest.approx(3.5 + 10)


def test_over_budget(service: ShowService, store: ShowStore) -> None:
    service.update_brief(store, {"duration_s": 20})

    estimate = service.estimate_timeline(store)

    assert estimate.over_budget_s == pytest.approx(estimate.total_s - 20)
    assert estimate.as_dict()["over_budget_s"] == estimate.over_budget_s

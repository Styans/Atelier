from pathlib import Path

import numpy as np
import pytest

from atelier_domain import AnimTrack
from atelier_show_engine.service import ServiceError, ShowService
from atelier_show_engine.store import COMMIT_LISTENERS, ConflictError, ShowStore


@pytest.fixture
def store(service: ShowService, fixture_files: dict[str, Path]) -> ShowStore:
    store, _ = service.create_show("Edits", drones=16, duration_s=120)
    service.add_model(store, fixture_files["torus"], name="Torus")
    service.add_model(store, fixture_files["star"], name="Star")
    service.add_scene(store, "m_torus", hold_s=5)
    service.add_scene(store, "m_star", hold_s=5)
    return store


def test_brief_and_flight_edits(service: ShowService, store: ShowStore) -> None:
    service.update_brief(
        store, {"title": "Новое имя", "palette": ["#ff0000"], "viewer_direction": "+X"}
    )
    service.update_flight(store, {"takeoff_altitude_m": 8})

    show = store.load()
    assert show.brief.title == "Новое имя" and show.brief.viewer_direction == "+X"
    assert show.flight.takeoff_altitude_m == 8
    with pytest.raises(ServiceError, match="cannot be edited"):
        service.update_brief(store, {"fleet": {}})
    with pytest.raises(ServiceError, match="palette"):
        service.update_brief(store, {"palette": ["red"]})


def test_stale_revision_is_a_conflict(service: ShowService, store: ShowStore) -> None:
    base = store.current_revision()
    service.update_brief(store, {"title": "A"})

    with pytest.raises(ConflictError, match="changed meanwhile"):
        service.check_revision(store, base)
    service.check_revision(store, base + 1)


def test_model_edits_validate_overrides(service: ShowService, store: ShowStore) -> None:
    service.update_model(
        store, "m_star", {"target_height_m": 25, "color_source": "solid", "palette": ["#ffcc00"]}
    )
    card = store.load().models["m_star"]
    assert card.target_height_m == 25 and card.version == 2

    with pytest.raises(ServiceError, match="no point_distributor"):
        service.update_model(
            store, "m_star", {"pipeline_overrides": {"point_distributor": {"component": "nope"}}}
        )


def test_model_in_use_cannot_be_removed(service: ShowService, store: ShowStore) -> None:
    with pytest.raises(ServiceError, match="used by scenes"):
        service.remove_model(store, "m_torus")

    service.remove_scene(store, "s_1")
    service.remove_model(store, "m_torus")

    assert "m_torus" not in store.load().models
    assert any(p.name.startswith("mesh_") for p in (store.root / "models" / "m_torus").iterdir())


def test_scene_edit_and_reorder(service: ShowService, store: ShowStore) -> None:
    track = AnimTrack(primitive="builtin.rotate", params={"angle_deg": 45}).model_dump(mode="json")
    service.update_scene(store, "s_2", {"hold_s": 9, "animation": {"tracks": [track]}})
    service.reorder_scenes(store, ["s_2", "s_1"])

    program = store.load().program
    assert [s.id for s in program] == ["s_2", "s_1"]
    assert program[0].hold_s == 9 and program[0].animation is not None
    with pytest.raises(ServiceError, match="every scene"):
        service.reorder_scenes(store, ["s_1"])
    with pytest.raises(ServiceError, match="no animation_primitive"):
        service.update_scene(
            store, "s_1", {"animation": {"tracks": [{"primitive": "builtin.nope"}]}}
        )


def test_revision_diff_is_readable(service: ShowService, store: ShowStore) -> None:
    before = store.current_revision()
    service.update_scene(store, "s_1", {"hold_s": 7})
    service.reorder_scenes(store, ["s_2", "s_1"])

    diff = {d.path: (d.old, d.new) for d in service.revision_diff(store, before)}

    assert diff["program.s_1.hold_s"] == ("5.0", "7.0")
    assert diff["program order"] == ('["s_1", "s_2"]', '["s_2", "s_1"]')


def test_commit_listeners_and_author(service: ShowService, store: ShowStore) -> None:
    seen: list[int] = []
    COMMIT_LISTENERS.append(lambda _store, show, info: seen.append(show.revision))
    try:
        service.author = "ui"
        service.approve_stage(store, "brief")
    finally:
        COMMIT_LISTENERS.clear()

    assert seen == [store.current_revision()]
    assert store.history()[-1].author == "ui"
    assert store.load().stage_status == {"brief": "approved"}


def test_points_preview_is_at_the_safe_scale(service: ShowService, store: ShowStore) -> None:
    service.compute_points(store, "m_torus")

    preview = service.points_preview(store, "m_torus")

    assert preview.positions.shape == (16, 3) and preview.colors.shape == (16, 3)
    assert float(np.min(preview.nearest_m)) == pytest.approx(2.0 * 2**0.5, rel=1e-3)
    assert preview.stale is None

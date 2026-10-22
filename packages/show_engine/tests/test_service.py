import zipfile
from pathlib import Path

import numpy as np
import pytest

from atelier_domain import AnimTrack
from atelier_plugin_sdk import CompiledShow
from atelier_show_engine.compiler import CompileError
from atelier_show_engine.service import ServiceError, ShowService
from atelier_show_engine.store import ShowStore

DRONES = 36


def build(service: ShowService, files: dict[str, Path]) -> ShowStore:
    store, _ = service.create_show("Тест", drones=DRONES, duration_s=300)
    service.set_music(store, files["music"])
    _, torus = service.add_model(store, files["torus"], name="Torus")
    _, star = service.add_model(store, files["star"], name="Star")
    service.compute_points(store, torus)
    service.compute_points(store, star)
    service.add_scene(store, torus, hold_s=8)
    spin = AnimTrack(primitive="builtin.rotate", easing="ease_in_out", params={"angle_deg": 90})
    service.add_scene(store, star, hold_s=10, tracks=[spin])
    return store


@pytest.fixture
def store(service: ShowService, fixture_files: dict[str, Path]) -> ShowStore:
    return build(service, fixture_files)


def test_full_pipeline_is_clean(service: ShowService, store: ShowStore) -> None:
    record, report = service.compile(store)
    summary = service.validate(store)
    exports = service.export(store, ["builtin.csv"])

    compiled = CompiledShow.load(store.path(record.path))
    assert compiled.drone_count == DRONES
    assert [m.kind for m in compiled.marks] == [
        "ground",
        "takeoff",
        "transition",
        "hold",
        "transition",
        "hold",
        "transition",
        "landing",
    ]
    assert np.allclose(compiled.positions[0, :, 2], 0) and np.allclose(
        compiled.positions[-1, :, 2], 0
    )
    assert summary.ok, [v.message for v in summary.violations]
    assert summary.metrics["min_distance_m"] >= 2.0
    assert compiled.audio is not None and compiled.audio.offset_s == pytest.approx(
        compiled.marks[1].end_frame / 30
    )
    assert not [w for w in report.warnings if "moves" in w]
    with zipfile.ZipFile(exports[0].files[0]) as zf:
        assert len(zf.namelist()) == DRONES
    assert any(f.suffix == ".wav" for f in exports[0].files)


def test_compile_is_deterministic(service: ShowService, store: ShowStore) -> None:
    first, _ = service.compile(store)
    second, _ = service.compile(store)

    assert first.content_hash == second.content_hash
    assert first.path != second.path  # a new file, never an overwrite


def test_every_step_is_a_revision(service: ShowService, store: ShowStore) -> None:
    messages = [r.message for r in store.history()]

    assert messages[0] == "Create show"
    assert any(m.startswith("Points for m_torus") for m in messages)
    before = store.load().revision
    store.rollback(2)
    assert store.load().revision == before + 1


def test_changing_point_params_makes_the_chain_stale(
    service: ShowService, store: ShowStore
) -> None:
    service.compile(store)
    service.validate(store)
    assert all(reason is None for reason in service.stale_report(store).values())

    service.set_stage(store, "point_distributor", "pointille", {"relax_iterations": 5})

    report = service.stale_report(store)
    assert report["models.m_torus.points.36"] == "parameters changed: relax_iterations: 20 -> 5"
    assert "stale" in (report["compiled"] or "")
    assert "stale" in (report["validation"] or "")
    assert report["models.m_torus.mesh"] is None
    with pytest.raises(CompileError, match="stale"):
        service.compile(store)
    with pytest.raises(ServiceError, match="stale"):
        service.validate(store)


def test_compile_needs_points(service: ShowService, fixture_files: dict[str, Path]) -> None:
    store, _ = service.create_show("No points", drones=DRONES, duration_s=60)
    _, torus = service.add_model(store, fixture_files["torus"])
    service.add_scene(store, torus, hold_s=5)

    with pytest.raises(CompileError, match="atelier points"):
        service.compile(store)


def test_unknown_primitive_is_refused(service: ShowService, store: ShowStore) -> None:
    with pytest.raises(ServiceError, match="no animation_primitive"):
        service.add_scene(store, "m_torus", hold_s=5, tracks=[AnimTrack(primitive="builtin.nope")])


def test_a_too_short_transition_is_reported(service: ShowService, store: ShowStore) -> None:
    service.add_scene(store, "m_torus", hold_s=5, transition_s=1.0)
    service.compute_points(store, "m_torus")  # unchanged inputs -> same fingerprint

    service.compile(store)
    summary = service.validate(store)

    assert not summary.ok
    assert {v.rule for v in summary.violations} & {"speed", "accel"}
    assert any(v.scene_id == "s_3" and v.segment == "transition" for v in summary.violations)

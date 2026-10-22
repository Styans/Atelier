"""Phase 1 acceptance (SPEC §9): from two test GLBs to a validated 150-drone show and a .blend.

The reference show: takeoff -> torus -> star turning 90 degrees -> landing, 30 fps, with music.
Golden values live in ``golden_reference.json``; regenerate them deliberately with
``ATELIER_UPDATE_GOLDEN=1 uv run pytest tests/e2e``.
"""

from __future__ import annotations

import importlib.util
import json
import os
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from atelier_blender_bridge import SCRIPTS_DIR, run_blender
from atelier_domain import AnimTrack
from atelier_plugin_host import PluginRegistry
from atelier_plugin_sdk import CompiledShow
from atelier_plugin_sdk.template import create_plugin
from atelier_show_engine.config import AtelierConfig, PathsConfig
from atelier_show_engine.service import ShowService
from atelier_show_engine.store import ShowStore

REPO = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).with_name("golden_reference.json")
BLENDER = Path(
    os.environ.get(
        "ATELIER_BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
    )
)
DRONES = 150


def fixtures(out: Path) -> dict[str, Path]:
    spec = importlib.util.spec_from_file_location(
        "mf", REPO / "tests" / "fixtures" / "make_fixtures.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    files: dict[str, Path] = module.make_all(out)
    return files


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("acceptance")


@pytest.fixture(scope="module")
def service(workspace: Path) -> Iterator[ShowService]:
    config = AtelierConfig(
        root=workspace,
        paths=PathsConfig(
            projects_dir=Path("projects"), plugin_dirs=[REPO / "plugins"], blender_exe=BLENDER
        ),
    )
    with ShowService(config) as svc:
        yield svc


def build_reference(service: ShowService, files: dict[str, Path]) -> ShowStore:
    store, _ = service.create_show("35 ЖЫЛ (reference)", drones=DRONES, duration_s=240, seed=35)
    service.set_music(store, files["music"])
    _, torus = service.add_model(store, files["torus"], name="Torus")
    _, star = service.add_model(store, files["star"], name="Star")
    service.compute_points(store, torus)
    service.compute_points(store, star)
    service.add_scene(store, torus, hold_s=15)
    turn = AnimTrack(primitive="builtin.rotate", easing="ease_in_out", params={"angle_deg": 90})
    service.add_scene(store, star, hold_s=20, tracks=[turn])
    return store


@pytest.fixture(scope="module")
def reference(service: ShowService, workspace: Path) -> tuple[ShowStore, CompiledShow]:
    store = build_reference(service, fixtures(workspace / "fixtures"))
    record, _ = service.compile(store)
    return store, CompiledShow.load(store.path(record.path))


def test_reference_show_is_valid(
    service: ShowService, reference: tuple[ShowStore, CompiledShow]
) -> None:
    store, compiled = reference

    summary = service.validate(store)
    report = json.loads(summary.path.read_text(encoding="utf-8"))

    assert compiled.drone_count == DRONES and compiled.fps == 30
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
    assert {r["component"] for r in report["rules"]} == {
        "builtin.min_distance",
        "builtin.speed",
        "builtin.accel",
        "builtin.altitude",
    }
    assert report["metrics"]["min_distance_m"] >= 2.0
    assert report["metrics"]["max_horizontal_speed_ms"] <= 4.0


def test_reference_show_matches_golden(reference: tuple[ShowStore, CompiledShow]) -> None:
    _, compiled = reference
    actual = {
        "frames": compiled.frame_count,
        "duration_s": round(compiled.duration_s, 3),
        "segments": [[m.kind, m.start_frame, m.end_frame] for m in compiled.marks],
        "content_hash": compiled.content_hash(),
    }
    if os.environ.get("ATELIER_UPDATE_GOLDEN") or not GOLDEN.exists():
        GOLDEN.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8", newline="\n")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert {k: v for k, v in actual.items() if k != "content_hash"} == {
        k: v for k, v in golden.items() if k != "content_hash"
    }
    assert actual["content_hash"] == golden["content_hash"], "compiled trajectories changed"


def test_reference_show_is_deterministic(service: ShowService, workspace: Path) -> None:
    files = fixtures(workspace / "fixtures_again")
    first = build_reference(service, files)
    second = build_reference(service, files)

    a, _ = service.compile(first)
    b, _ = service.compile(second)

    assert a.content_hash == b.content_hash


def test_swapping_pointille_for_a_new_plugin_needs_no_core_change(
    workspace: Path, tmp_path: Path
) -> None:
    plugins = tmp_path / "plugins"
    create_plugin(plugins, "demo_distributor", "point_distributor", namespace="demo")
    config = AtelierConfig(
        root=tmp_path,
        paths=PathsConfig(projects_dir=Path("projects"), plugin_dirs=[plugins, REPO / "plugins"]),
    )
    with ShowService(
        config, PluginRegistry.discover(config.plugin_dirs, use_entry_points=False)
    ) as svc:
        store = build_reference(svc, fixtures(tmp_path / "fixtures"))
        svc.compile(store)

        svc.set_stage(store, "point_distributor", "demo_distributor", {})
        stale = svc.stale_report(store)
        assert "component changed: pointille.blue_noise -> demo.random" in (
            stale["models.m_torus.points.150"] or ""
        )
        assert stale["compiled"] is not None

        for model in ("m_torus", "m_star"):
            record = svc.compute_points(store, model)
            assert record.producer.component == "demo.random"
        record, _ = svc.compile(store)

    assert CompiledShow.load(store.path(record.path)).drone_count == DRONES


@pytest.mark.blender
@pytest.mark.skipif(not BLENDER.is_file(), reason="Blender not installed")
def test_blender_builds_a_blend_that_skybrush_opens(
    service: ShowService, reference: tuple[ShowStore, CompiledShow], workspace: Path
) -> None:
    store, compiled = reference
    service.validate(store)

    exports = service.export(store, ["blender_skybrush"])
    blend = exports[0].folder / "show.blend"
    build = json.loads((exports[0].folder / "build_report.json").read_text(encoding="utf-8"))
    frames = [0, compiled.marks[3].start_frame, compiled.frame_count - 1]
    seen = run_blender(
        BLENDER,
        SCRIPTS_DIR / "inspect_show.py",
        {"blend": str(blend), "addon": "ui_skybrush_studio", "frames": frames},
        workdir=workspace / "inspect",
        log_path=workspace / "inspect" / "blender.log",
        timeout_s=600,
    )

    assert build["max_error_m"] < 0.05
    assert build["reduction"]["ratio"] < 0.5
    assert seen["drones"] == DRONES and seen["drone_names"][0] == "Drone 1"
    assert seen["fps"] == 30 and seen["frame_range"] == [1, compiled.frame_count]
    assert [e["purpose"] for e in seen["storyboard"]] == ["TAKEOFF", "SHOW", "SHOW", "LANDING"]
    assert all(e["transition"] == "MANUAL" for e in seen["storyboard"])
    assert seen["sound_strips"] == ["music"]
    assert seen["safety"]["proximity_warning_threshold"] == pytest.approx(2.0)
    for frame in frames:
        reopened = np.array(seen["positions"][str(frame)])
        assert np.abs(reopened - compiled.positions[frame]).max() < 0.05

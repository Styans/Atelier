from pathlib import Path

import numpy as np
import pytest

from atelier_builtin_validators import (
    Accel,
    AccelParams,
    Altitude,
    AltitudeParams,
    MinDistance,
    MinDistanceParams,
    Speed,
    SpeedParams,
    plugin,
    windows,
)
from atelier_domain import fleet_from_preset
from atelier_plugin_sdk import CompiledShow, CompiledShowRef, RunContext, SceneMark
from atelier_plugin_sdk.testing import LocalInvoker, reference_compiled_show, run_contract_suite

FLEET = fleet_from_preset("conservative", 2)
FPS = 30


def save(
    tmp_path: Path, positions: np.ndarray, marks: list[SceneMark] | None = None
) -> CompiledShowRef:
    show = CompiledShow(
        positions=positions,
        colors=np.zeros(positions.shape, dtype=np.uint8),
        fps=FPS,
        marks=marks or [SceneMark(kind="hold", start_frame=0, end_frame=len(positions) - 1)],
    )
    return CompiledShowRef(path=show.save(tmp_path / "show.npz"))


def ctx(tmp_path: Path) -> RunContext:
    return RunContext(seed=0, workdir=tmp_path)


def test_windows_finds_runs() -> None:
    assert list(windows(np.array([0, 1, 1, 0, 1], dtype=bool))) == [(1, 2), (4, 4)]


def test_min_distance_reports_the_window_and_the_drones(tmp_path: Path) -> None:
    frames = 61
    gap = np.concatenate([np.linspace(6, 1, 31), np.linspace(1, 6, 30)])  # 6 m -> 1 m -> 6 m
    positions = np.zeros((frames, 2, 3))
    positions[:, 0] = [0, 0, 10]
    positions[:, 1, 0] = gap
    positions[:, 1, 2] = 10

    result = MinDistance().check(
        ctx(tmp_path), save(tmp_path, positions), FLEET, MinDistanceParams()
    )

    assert len(result.violations) == 1
    violation = result.violations[0]
    assert violation.drones == [0, 1]
    assert violation.value == pytest.approx(1.0)
    assert violation.t_start_s < 1.0 < violation.t_end_s
    assert result.metrics["min_distance_m"] == pytest.approx(1.0)


def test_min_distance_ignores_drones_on_the_ground(tmp_path: Path) -> None:
    positions = np.zeros((3, 2, 3))
    positions[:, 1, 0] = 0.5  # 0.5 m apart, but at z = 0

    result = MinDistance().check(
        ctx(tmp_path), save(tmp_path, positions), FLEET, MinDistanceParams()
    )

    assert not result.violations


def test_speed_flags_each_direction(tmp_path: Path) -> None:
    t = np.arange(31) / FPS
    positions = np.zeros((31, 2, 3))
    positions[:, 0] = np.column_stack([10 * t, np.zeros(31), np.full(31, 10)])  # 10 m/s sideways
    positions[:, 1] = np.column_stack([np.full(31, 50), np.zeros(31), 10 + 3 * t])  # 3 m/s climb

    result = Speed().check(ctx(tmp_path), save(tmp_path, positions), FLEET, SpeedParams())

    kinds = {v.message.split(" ")[0]: v for v in result.violations}
    assert kinds["horizontal"].drones == [0]
    assert kinds["horizontal"].value == pytest.approx(10, rel=1e-3)
    assert kinds["climb"].drones == [1]
    assert "descent" not in kinds


def test_accel_catches_an_abrupt_stop(tmp_path: Path) -> None:
    t = np.arange(61) / FPS
    x = np.where(t < 1, 2 * t, 2.0)  # 2 m/s, then stops in one frame
    positions = np.zeros((61, 1, 3))
    positions[:, 0, 0], positions[:, 0, 2] = x, 10

    result = Accel().check(ctx(tmp_path), save(tmp_path, positions), FLEET, AccelParams())

    assert len(result.violations) == 1
    assert result.violations[0].value > 50


def test_altitude_ceiling_and_floor(tmp_path: Path) -> None:
    positions = np.zeros((10, 2, 3))
    positions[:, 0, 2] = 200  # above the 120 m ceiling
    positions[:, 1, 2] = 1  # below 2.5 m during a hold
    marks = [
        SceneMark(kind="ground", start_frame=0, end_frame=4),
        SceneMark(kind="hold", start_frame=5, end_frame=9),
    ]

    result = Altitude().check(
        ctx(tmp_path), save(tmp_path, positions, marks), FLEET, AltitudeParams()
    )

    assert [(v.drones, v.frame_start) for v in result.violations] == [([0], 0), ([1], 5)]


@pytest.mark.parametrize("component", plugin.components, ids=lambda c: c.name)
def test_contract_and_calm_show(component: object, tmp_path: Path) -> None:
    results = run_contract_suite(LocalInvoker(component), tmp_path)  # type: ignore[arg-type]

    assert all(r.ok for r in results), results


def test_calm_reference_show_is_clean(tmp_path: Path) -> None:
    show = reference_compiled_show()
    ref = CompiledShowRef(path=show.save(tmp_path / "ref.npz"))
    fleet = fleet_from_preset("conservative", show.drone_count)

    for component in plugin.components:
        result = component.check(ctx(tmp_path), ref, fleet, component.Params())  # type: ignore[attr-defined]
        assert not result.violations, (component.name, result.violations)

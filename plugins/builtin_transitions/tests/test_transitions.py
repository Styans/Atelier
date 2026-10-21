import math
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.distance import pdist

from atelier_builtin_transitions import (
    HungarianDirect,
    HungarianDirectParams,
    minimal_duration,
    plugin,
)
from atelier_domain import fleet_from_preset
from atelier_plugin_sdk import RunContext
from atelier_plugin_sdk.testing import LocalInvoker, run_contract_suite

FLEET = fleet_from_preset("conservative", 64)


def plan(start: np.ndarray, targets: np.ndarray, duration: float | None = None, tmp: Path = Path()):  # type: ignore[no-untyped-def]
    ctx = RunContext(seed=0, workdir=tmp)
    return HungarianDirect().plan(ctx, start, targets, FLEET, 30, duration, HungarianDirectParams())


def test_assignment_minimizes_squared_distance() -> None:
    start = np.array([[0.0, 0, 10], [10.0, 0, 10]])
    targets = np.array([[10.0, 0, 12], [0.0, 0, 12]])  # swapped order

    result = plan(start, targets)

    assert result.assignment.tolist() == [1, 0]
    assert np.allclose(result.positions[0], start)
    assert np.allclose(result.positions[-1], targets[result.assignment])


def test_free_duration_respects_the_fleet_limits() -> None:
    rng = np.random.default_rng(1)
    start = rng.uniform(-20, 20, (64, 3)) + np.array([0.0, 0.0, 30.0])
    targets = rng.uniform(-20, 20, (64, 3)) + np.array([0.0, 0.0, 30.0])

    result = plan(start, targets)

    v = np.diff(result.positions, axis=0) * 30
    a = np.diff(v, axis=0) * 30
    assert np.linalg.norm(v[..., :2], axis=2).max() <= FLEET.max_speed_h_ms
    assert v[..., 2].max() <= FLEET.max_speed_up_ms
    assert (-v[..., 2]).max() <= FLEET.max_speed_down_ms
    assert np.linalg.norm(a, axis=2).max() <= FLEET.max_accel_ms2
    assert result.duration_s >= minimal_duration(targets[result.assignment] - start, FLEET)


def test_requested_duration_is_honoured() -> None:
    start = np.zeros((4, 3)) + np.arange(4)[:, None] * [3, 0, 0]

    result = plan(start, start + np.array([0.0, 0.0, 5.0]), duration=4.0)

    assert result.duration_s == pytest.approx(4.0)
    assert len(result.positions) == 121


def test_capt_spacing_keeps_transitions_safe() -> None:
    """Formations spaced sqrt(2) x d apart never come closer than d during a transition."""
    d = FLEET.min_distance_m
    side = 6
    grid = np.array([(i, j, 0.0) for i in range(side) for j in range(side)]) * math.sqrt(2) * d
    rng = np.random.default_rng(7)
    for _ in range(5):
        rotation = rng.uniform(0, 2 * np.pi)
        c, s = math.cos(rotation), math.sin(rotation)
        targets = grid @ np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]).T + rng.uniform(-30, 30, 3)
        lift = np.array([0.0, 0.0, 20.0])
        result = plan(grid + lift, targets + lift)
        closest = min(pdist(frame).min() for frame in result.positions)
        assert closest >= d - 1e-9


def test_contract(tmp_path: Path) -> None:
    results = run_contract_suite(LocalInvoker(plugin.components[0]), tmp_path)

    assert all(r.ok for r in results), results

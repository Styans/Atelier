from pathlib import Path

import numpy as np
import pytest

from atelier_builtin_animation import Rotate, RotateParams, ScalePulse, ScalePulseParams, plugin
from atelier_plugin_sdk.testing import LocalInvoker, run_contract_suite


def frames(points: list[list[float]], count: int) -> np.ndarray:
    return np.repeat(np.array(points, dtype=float)[None], count, axis=0)


def test_rotate_quarter_turn_around_z() -> None:
    pts = frames([[1, 0, 0], [0, 0, 5]], 2)

    out = Rotate().evaluate(
        pts, np.array([True, True]), np.zeros(2), np.array([0.0, 1.0]), RotateParams(angle_deg=90)
    )

    assert np.allclose(out[0], pts[0])
    assert np.allclose(out[1], [[0, 1, 0], [0, 0, 5]])


def test_rotate_respects_mask_and_pivot() -> None:
    pts = frames([[2, 0, 0], [3, 0, 0]], 1)
    params = RotateParams(axis=(0, 0, 2), angle_deg=180, pivot=(1, 0, 0))

    out = Rotate().evaluate(pts, np.array([True, False]), np.zeros(1), np.ones(1), params)

    assert np.allclose(out[0, 0], [0, 0, 0])
    assert np.allclose(out[0, 1], [3, 0, 0])


def test_rotate_rejects_a_zero_axis() -> None:
    with pytest.raises(ValueError, match="zero"):
        Rotate().evaluate(
            frames([[1, 0, 0]], 1),
            np.array([True]),
            np.zeros(1),
            np.ones(1),
            RotateParams(axis=(0, 0, 0)),
        )


def test_scale_pulse_peaks_at_a_quarter_period() -> None:
    pts = frames([[2, 0, 0]], 2)
    params = ScalePulseParams(amplitude=0.25, frequency_hz=1.0)

    out = ScalePulse().evaluate(pts, np.array([True]), np.array([0.0, 0.25]), np.zeros(2), params)

    assert np.allclose(out[0, 0], [2, 0, 0])
    assert np.allclose(out[1, 0], [2.5, 0, 0])


@pytest.mark.parametrize("component", plugin.components, ids=lambda c: c.name)
def test_contract(component: object, tmp_path: Path) -> None:
    results = run_contract_suite(LocalInvoker(component), tmp_path)  # type: ignore[arg-type]

    assert all(r.ok for r in results), results

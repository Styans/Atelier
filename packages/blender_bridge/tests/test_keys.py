import numpy as np
import pytest

from atelier_blender_bridge import build_keys, decimate


def interpolate(frames: np.ndarray, kept: np.ndarray, values: np.ndarray) -> np.ndarray:
    return np.column_stack(
        [np.interp(frames, kept, values[kept, c]) for c in range(values.shape[1])]
    )


def test_straight_line_needs_two_keys() -> None:
    values = np.column_stack([np.linspace(0, 10, 100), np.zeros(100), np.full(100, 5.0)])

    assert decimate(values, 0.01).tolist() == [0, 99]


def test_curve_stays_within_tolerance() -> None:
    t = np.linspace(0, 2 * np.pi, 600)
    values = np.column_stack([10 * np.cos(t), 10 * np.sin(t), t])

    kept = decimate(values, 0.02)

    error = np.linalg.norm(interpolate(np.arange(600), kept, values) - values, axis=1).max()
    assert error <= 0.02
    assert len(kept) < 150


def test_corners_are_kept() -> None:
    values = np.zeros((21, 1))
    values[10:, 0] = np.arange(11)  # flat, then a ramp

    assert 10 in decimate(values, 1e-6)


@pytest.mark.parametrize("frames", [1, 2])
def test_tiny_inputs(frames: int) -> None:
    assert decimate(np.zeros((frames, 3)), 0.1).tolist() == list(range(frames))


def test_build_keys_offsets() -> None:
    positions = np.zeros((50, 3, 3), dtype=np.float32)
    positions[:, 1, 0] = np.linspace(0, 5, 50)
    colors = np.zeros((50, 3, 3), dtype=np.uint8)

    keys = build_keys(positions, colors, 0.01, 0.01)

    assert keys["loc_offsets"].tolist() == [0, 2, 4, 6]
    assert keys["col_offsets"].tolist() == [0, 2, 4, 6]
    assert keys["loc_values"].shape == (6, 3)

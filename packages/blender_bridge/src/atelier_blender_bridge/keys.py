"""Reducing per-frame trajectories to keyframes with linear interpolation.

A 500-drone, 10-minute show at 30 fps is 63 M keys (about 700 MB of .blend); holds and
straight flights need only a few. Douglas-Peucker on each drone's curve keeps a frame only
where linear interpolation between the kept neighbours would miss by more than the tolerance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]
Indices = npt.NDArray[np.int64]


def decimate(values: Array, tolerance: float) -> Indices:
    """Frame indices to keep so that linear interpolation stays within ``tolerance``.

    ``values`` has shape (frames, channels); the error is the Euclidean distance.
    """
    frames = len(values)
    if frames <= 2:
        return np.arange(frames, dtype=np.int64)
    keep = np.zeros(frames, dtype=bool)
    keep[[0, -1]] = True
    stack = [(0, frames - 1)]
    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue
        inner = np.arange(start + 1, end)
        weight = ((inner - start) / (end - start))[:, None]
        line = values[start] * (1 - weight) + values[end] * weight
        error = np.linalg.norm(values[inner] - line, axis=1)
        worst = int(np.argmax(error))
        if error[worst] > tolerance:
            split = int(inner[worst])
            keep[split] = True
            stack += [(start, split), (split, end)]
    return np.flatnonzero(keep).astype(np.int64)


def build_keys(
    positions: npt.NDArray[np.floating],
    colors: npt.NDArray[np.uint8],
    position_tol: float,
    color_tol: float,
) -> dict[str, np.ndarray[Any, Any]]:
    """Keyframes of every drone in compressed-row form.

    Returns ``loc_frames``, ``loc_values`` (K, 3), ``loc_offsets`` (D + 1) and the same for
    ``col_*`` with colours in [0, 1] (sRGB LED values, what Skybrush reads from Object.color).
    """
    drones = positions.shape[1]
    parts: dict[str, list[np.ndarray[Any, Any]]] = {k: [] for k in ("lf", "lv", "cf", "cv")}
    loc_offsets, col_offsets = [0], [0]
    for drone in range(drones):
        p = positions[:, drone].astype(np.float64)
        c = colors[:, drone].astype(np.float64) / 255.0
        li, ci = decimate(p, position_tol), decimate(c, color_tol)
        parts["lf"].append(li)
        parts["lv"].append(p[li])
        parts["cf"].append(ci)
        parts["cv"].append(c[ci])
        loc_offsets.append(loc_offsets[-1] + len(li))
        col_offsets.append(col_offsets[-1] + len(ci))
    return {
        "loc_frames": np.concatenate(parts["lf"]),
        "loc_values": np.concatenate(parts["lv"]),
        "loc_offsets": np.array(loc_offsets, dtype=np.int64),
        "col_frames": np.concatenate(parts["cf"]),
        "col_values": np.concatenate(parts["cv"]),
        "col_offsets": np.array(col_offsets, dtype=np.int64),
    }


def save_keys(
    path: Path, keys: dict[str, np.ndarray[Any, Any]], **extra: np.ndarray[Any, Any]
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {**keys, **extra}
    with path.open("wb") as fh:
        np.savez_compressed(fh, **payload)
    return path

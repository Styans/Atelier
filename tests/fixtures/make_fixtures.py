"""Small test inputs, generated so no binaries live in git.

    uv run python tests/fixtures/make_fixtures.py [out_dir]   # default: tests/fixtures/generated

Both models are glTF (Y-up, facing +Z) with vertex colours, like Pygmalion's output.
"""

from __future__ import annotations

import math
import sys
import wave
from pathlib import Path

import numpy as np
import trimesh


def torus_glb(path: Path) -> Path:
    """A ring standing upright, facing the viewer, with a rainbow around it."""
    mesh = trimesh.creation.torus(
        major_radius=1.0, minor_radius=0.3, major_sections=48, minor_sections=16
    )
    angle = np.arctan2(mesh.vertices[:, 1], mesh.vertices[:, 0])
    hue = (angle + np.pi) / (2 * np.pi)
    rgb = np.column_stack([0.5 + 0.5 * np.cos(2 * np.pi * (hue + k / 3)) for k in range(3)])
    mesh.visual = trimesh.visual.ColorVisuals(mesh, vertex_colors=(rgb * 255).astype(np.uint8))
    mesh.export(path, file_type="glb")
    return path


def star_glb(
    path: Path, points: int = 5, outer: float = 1.0, inner: float = 0.45, depth: float = 0.3
) -> Path:
    """A gold five-pointed star prism standing upright, facing the viewer."""
    ring = []
    for i in range(2 * points):
        radius = outer if i % 2 == 0 else inner
        a = math.pi / 2 + i * math.pi / points
        ring.append((radius * math.cos(a), radius * math.sin(a)))
    n = len(ring)
    front = [(x, y, depth / 2) for x, y in ring]
    back = [(x, y, -depth / 2) for x, y in ring]
    vertices = np.array([*front, *back, (0, 0, depth / 2), (0, 0, -depth / 2)])
    cf, cb = 2 * n, 2 * n + 1
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces += [(cf, i, j), (cb, n + j, n + i), (i, n + i, n + j), (i, n + j, j)]
    mesh = trimesh.Trimesh(vertices=vertices, faces=np.array(faces), process=False)
    gold = np.tile([255, 200, 40, 255], (len(vertices), 1)).astype(np.uint8)
    mesh.visual = trimesh.visual.ColorVisuals(mesh, vertex_colors=gold)
    mesh.export(path, file_type="glb")
    return path


def music_wav(path: Path, seconds: float = 60.0, rate: int = 8000) -> Path:
    t = np.arange(int(seconds * rate)) / rate
    samples = (0.2 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())
    return path


def make_all(out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return {
        "torus": torus_glb(out_dir / "torus.glb"),
        "star": star_glb(out_dir / "star.glb"),
        "music": music_wav(out_dir / "music.wav"),
    }


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "generated"
    for name, file in make_all(target).items():
        print(f"{name}: {file}")

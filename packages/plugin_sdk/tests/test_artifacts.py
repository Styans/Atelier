from pathlib import Path

import numpy as np
import pytest

from atelier_plugin_sdk import (
    CompiledShow,
    MeshData,
    PointSet,
    SceneMark,
    hex_to_linear,
    linear_to_srgb8,
    srgb_to_linear,
)
from atelier_plugin_sdk.testing import reference_mesh


def test_srgb_round_trip() -> None:
    values = np.linspace(0, 1, 256)

    assert np.array_equal(
        linear_to_srgb8(srgb_to_linear(values)), np.round(values * 255).astype(np.uint8)
    )
    assert np.allclose(hex_to_linear("#ffffff"), 1.0)
    assert np.allclose(hex_to_linear("#000000"), 0.0)


def test_mesh_round_trip_and_hash(tmp_path: Path) -> None:
    mesh = reference_mesh()

    loaded = MeshData.load(mesh.save(tmp_path / "mesh.npz"))

    assert np.array_equal(loaded.vertices, mesh.vertices)
    assert np.array_equal(loaded.faces, mesh.faces)
    assert loaded.content_hash() == mesh.content_hash()
    assert len(mesh.faces) == 128


def test_mesh_rejects_bad_faces() -> None:
    with pytest.raises(ValueError, match="out of range"):
        MeshData(vertices=np.zeros((3, 3)), faces=np.array([[0, 1, 3]]))


def test_sample_colors_interpolates_vertex_colors() -> None:
    mesh = MeshData(
        vertices=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float),
        faces=np.array([[0, 1, 2]]),
        vertex_colors=np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32),
    )

    colors = mesh.sample_colors([0, 0], [[1, 0, 0], [1 / 3, 1 / 3, 1 / 3]])

    assert np.allclose(colors[0], [1, 0, 0])
    assert np.allclose(colors[1], [1 / 3, 1 / 3, 1 / 3], atol=1e-6)


def test_sample_colors_reads_texture_through_uv() -> None:
    texture = np.zeros((2, 2, 3), dtype=np.uint8)
    texture[0, :] = 255  # top row white, bottom row black
    mesh = MeshData(
        vertices=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float),
        faces=np.array([[0, 1, 2]]),
        uv=np.array([[0, 1], [0, 1], [0, 1]], dtype=np.float32),  # all at the top
        texture=texture,
    )

    assert np.allclose(mesh.sample_colors([0], [[1, 0, 0]]), 1.0)


def test_point_set_validates_shapes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="normals"):
        PointSet(
            positions=np.zeros((2, 3)),
            normals=np.zeros((3, 3)),
            colors=np.zeros((2, 3)),
            tri_index=np.zeros(2),
            barycentric=np.zeros((2, 3)),
        )


def test_compiled_show_round_trip(tmp_path: Path) -> None:
    show = CompiledShow(
        positions=np.random.default_rng(0).random((5, 4, 3)),
        colors=np.full((5, 4, 3), 7, dtype=np.uint8),
        fps=30,
        marks=[SceneMark(kind="hold", scene_id="s_1", start_frame=0, end_frame=4)],
        meta={"title": "x"},
    )

    loaded = CompiledShow.load(show.save(tmp_path / "show.npz"))

    assert loaded.content_hash() == show.content_hash()
    assert loaded.marks == show.marks
    assert loaded.mark_at(3) == show.marks[0]
    assert loaded.duration_s == pytest.approx(4 / 30)

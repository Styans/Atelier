from pathlib import Path

import numpy as np
import pytest
from scipy.spatial import cKDTree

from atelier_plugin_sdk import MeshData, MeshRef, PointSet, RunContext
from atelier_plugin_sdk.testing import LocalInvoker, reference_mesh, run_contract_suite
from atelier_pointille import BlueNoise, BlueNoiseParams, plugin, pointille_revision, to_pointille


def distribute(tmp_path: Path, mesh: MeshData, n: int, seed: int = 0, **params: object) -> PointSet:
    ref = MeshRef(path=mesh.save(tmp_path / f"mesh_{seed}.npz"))
    ctx = RunContext(seed=seed, workdir=tmp_path / f"w{seed}")
    return BlueNoise().distribute(ctx, ref, n, BlueNoiseParams(**params)).load()  # type: ignore[arg-type]


def test_pointille_comes_from_a_pinned_commit() -> None:
    revision = pointille_revision()

    assert "@" in revision  # version @ git commit
    assert plugin.health_check().status == "ok"


@pytest.mark.parametrize("n", [1, 17, 150])
def test_exactly_n_points_on_the_surface(tmp_path: Path, n: int) -> None:
    mesh = reference_mesh(subdivisions=3)

    points = distribute(tmp_path, mesh, n)

    assert len(points) == n
    on_surface = np.einsum("nk,nkd->nd", points.barycentric, mesh.triangles()[points.tri_index])
    assert np.allclose(on_surface, points.positions, atol=1e-9)


def test_outline_keeps_the_count(tmp_path: Path) -> None:
    mesh = reference_mesh(subdivisions=3)

    points = distribute(tmp_path, mesh, 120, outline=True, outline_amount="share")

    assert len(points) == 120
    on_surface = np.einsum("nk,nkd->nd", points.barycentric, mesh.triangles()[points.tri_index])
    assert np.allclose(on_surface, points.positions, atol=1e-9)


def test_deterministic_per_seed(tmp_path: Path) -> None:
    mesh = reference_mesh()

    a = distribute(tmp_path / "a", mesh, 40, seed=3)
    b = distribute(tmp_path / "b", mesh, 40, seed=3)
    c = distribute(tmp_path / "c", mesh, 40, seed=4)

    assert a.content_hash() == b.content_hash()
    assert a.content_hash() != c.content_hash()


def test_points_are_evenly_spread(tmp_path: Path) -> None:
    points = distribute(tmp_path, reference_mesh(subdivisions=3), 150)

    metrics = points.meta["metrics"]
    assert metrics["min_over_ideal"] > 0.7
    assert metrics["cv_nn"] < 0.15


def test_evaluate_follows_the_mesh(tmp_path: Path) -> None:
    mesh = reference_mesh()
    points = distribute(tmp_path, mesh, 30)
    angle = np.radians(30)
    rotation = np.array(
        [[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]]
    )

    same = points.evaluate(mesh.faces, mesh.vertices)
    moved = points.evaluate(mesh.faces, mesh.vertices @ rotation.T * 2 + [0, 0, 5])

    assert np.allclose(same, points.positions)
    assert np.allclose(moved, points.positions @ rotation.T * 2 + [0, 0, 5])


def test_colours_come_from_vertex_colours(tmp_path: Path) -> None:
    mesh = reference_mesh()
    mesh.vertex_colors = np.tile(
        np.array([[1.0, 0.0, 0.0]], dtype=np.float32), (len(mesh.vertices), 1)
    )

    points = distribute(tmp_path, mesh, 20)

    assert np.allclose(points.colors, [1, 0, 0], atol=1e-5)


def test_soup_keeps_face_order() -> None:
    mesh = reference_mesh()

    soup = to_pointille(mesh)

    assert np.array_equal(soup.triangles, mesh.triangles())


def test_scaling_by_min_spacing_reaches_the_target(tmp_path: Path) -> None:
    points = distribute(tmp_path, reference_mesh(subdivisions=3), 100)
    nn = cKDTree(points.positions).query(points.positions, k=2)[0][:, 1].min()

    scale = 2.0 / nn

    scaled = points.positions * scale
    assert cKDTree(scaled).query(scaled, k=2)[0][:, 1].min() == pytest.approx(2.0)


def test_contract(tmp_path: Path) -> None:
    results = run_contract_suite(LocalInvoker(plugin.components[0]), tmp_path)

    assert all(r.ok for r in results), results

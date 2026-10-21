"""Pointille as an Atelier ``point_distributor``.

The Pointille core is used as is: this adapter converts the SDK's indexed ``MeshData`` into
Pointille's triangle soup *in face order*, so Pointille's ``triangle_ids`` are the face
indices of the source mesh and ``PointSet.evaluate`` can follow deformations.
"""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, distribution
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
from pointille.core import DistributeOptions, Material, Texture, distribute
from pointille.core import MeshData as PointilleMesh
from pydantic import BaseModel, Field
from scipy.spatial import cKDTree

from atelier_plugin_sdk import (
    ContourRef,
    Health,
    MeshData,
    MeshRef,
    Plugin,
    PluginError,
    PointDistributor,
    PointSet,
    PointSetRef,
    RunContext,
)

Vec3 = tuple[float, float, float]


def pointille_revision() -> str:
    """Commit of the installed Pointille (from uv's install metadata), or its version."""
    try:
        dist = distribution("pointille")
    except PackageNotFoundError:
        return "unknown"
    direct = dist.read_text("direct_url.json")
    commit = json.loads(direct).get("vcs_info", {}).get("commit_id") if direct else None
    return f"{dist.version}@{commit[:10]}" if commit else dist.version


def to_pointille(mesh: MeshData) -> PointilleMesh:
    """Triangle soup in face order: soup triangle i is face i of ``mesh``."""
    faces = mesh.faces
    uvs = mesh.uv[faces] if mesh.uv is not None else None
    texture = (
        Texture(pixels=mesh.texture, srgb=True)
        if mesh.texture is not None and uvs is not None
        else None
    )
    return PointilleMesh(
        triangles=mesh.triangles(),
        uvs=uvs,
        material_ids=np.zeros(len(faces), dtype=np.int64),
        vertex_colors=mesh.vertex_colors[faces] if mesh.vertex_colors is not None else None,
        materials=[Material(base_color=mesh.base_color, texture=texture)],
    )


def spacing_metrics(positions: npt.NDArray[np.float64], area: float) -> dict[str, float]:
    """Nearest-neighbour spacing: min, mean, coefficient of variation and ratios to ideal."""
    n = len(positions)
    if n < 2:
        return {}
    distances, _ = cKDTree(positions).query(positions, k=2)
    nn = distances[:, 1]
    ideal = float(np.sqrt(2 * area / (np.sqrt(3) * n))) if area > 0 else 0.0  # hexagonal packing
    metrics = {
        "min_nn_m": float(nn.min()),
        "mean_nn_m": float(nn.mean()),
        "cv_nn": float(nn.std() / nn.mean()) if nn.mean() > 0 else 0.0,
    }
    if ideal > 0:
        metrics |= {
            "min_over_ideal": metrics["min_nn_m"] / ideal,
            "mean_over_ideal": metrics["mean_nn_m"] / ideal,
        }
    return metrics


class BlueNoiseParams(BaseModel):
    method: Literal["blue_noise"] = Field(
        "blue_noise",
        description="Weighted sample elimination + relaxation (Pointille's only method)",
    )
    oversample: float = Field(8.0, ge=2.0, le=64.0, description="Candidates per drone")
    relax_iterations: int = Field(20, ge=0, le=200, description="0 = pure sample elimination")
    color_mode: Literal["area", "point", "none"] = Field(
        "area", description="area: mean colour of the drone's patch; point: colour under it"
    )
    min_brightness: float = Field(0.0, ge=0.0, le=1.0, description="LEDs never darker than this")
    facade: bool = Field(False, description="Put drones where the audience sees the figure")
    view_dir: Vec3 = Field((0.0, -1.0, 0.0), description="Figure -> audience, figure-local")
    facade_angle: float = Field(80.0, gt=0.0, le=90.0)
    audience_spread: float = Field(0.0, ge=0.0, le=180.0)
    hidden_density: float = Field(0.0, ge=0.0, le=1.0)


class BlueNoise(PointDistributor[BlueNoiseParams]):
    """Pointille: exactly n evenly spaced drones on the surface, coloured from the model."""

    name = "blue_noise"
    version = f"0.1.0+pointille.{pointille_revision()}"
    Params = BlueNoiseParams

    def distribute(
        self, ctx: RunContext, source: MeshRef | ContourRef, n: int, params: BlueNoiseParams
    ) -> PointSetRef:
        if not isinstance(source, MeshRef):
            raise PluginError("Pointille distributes over surfaces; 2D contours are not supported")
        mesh = source.load()
        options = DistributeOptions(
            count=n,
            seed=ctx.seed,
            oversample=params.oversample,
            relax_iterations=params.relax_iterations,
            facade=params.facade,
            view_dir=params.view_dir,
            facade_angle=params.facade_angle,
            audience_spread=params.audience_spread,
            hidden_density=params.hidden_density,
            color_mode=params.color_mode,
            min_brightness=params.min_brightness,
            up_axis=2,
        )
        ctx.progress(0.05, f"distributing {n} drones")
        result = distribute(to_pointille(mesh), options)
        colors = np.asarray(
            result.colors_linear if params.color_mode != "none" else np.ones((n, 3)),
            dtype=np.float32,
        )
        meta: dict[str, Any] = {
            "pointille": {
                "min_distance": result.min_distance,
                "mean_distance": result.mean_distance,
                "area": result.area,
            },
            "metrics": spacing_metrics(result.positions, result.area),
        }
        if result.visibility is not None:
            meta["visibility_mean"] = float(np.mean(result.visibility))
        points = PointSet(
            positions=result.positions,
            normals=result.normals,
            colors=colors,
            tri_index=result.triangle_ids,
            barycentric=result.barycentrics,
            meta=meta,
        )
        ctx.progress(1.0, f"min spacing {result.min_distance:.3f} (model units)")
        return ctx.save(points, "points.npz")


def _health() -> Health:
    revision = pointille_revision()
    return (
        Health.ok(f"pointille {revision}")
        if revision != "unknown"
        else Health.error("pointille not installed")
    )


plugin = Plugin(components=[BlueNoise()], health_check=_health)

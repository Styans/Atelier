"""A test plugin. It prints to stdout on purpose: the subprocess server must survive that."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field

from atelier_plugin_sdk import (
    AnimationPrimitive,
    Health,
    MeshRef,
    Plugin,
    PointDistributor,
    PointSet,
    PointSetRef,
    ResourceExhausted,
    RunContext,
)

print("fixture_plugin imported (this line must not reach the protocol)")


class RandomParams(BaseModel):
    area_weighted: bool = True


class RandomDistributor(PointDistributor[RandomParams]):
    """Uniform random points."""

    name = "random"
    Params = RandomParams

    def distribute(
        self, ctx: RunContext, source: MeshRef, n: int, params: RandomParams
    ) -> PointSetRef:
        print("distributing...")  # stdout noise during a call
        mesh = source.load()
        tris = mesh.triangles()
        weights = mesh.face_areas() if params.area_weighted else np.ones(len(tris))
        rng = ctx.rng()
        tri_index = rng.choice(len(tris), size=n, p=weights / weights.sum())
        r1, r2 = np.sqrt(rng.random(n)), rng.random(n)
        bary = np.stack([1 - r1, r1 * (1 - r2), r1 * r2], axis=1)
        ctx.progress(0.5, "half way")
        points = PointSet(
            positions=np.einsum("nk,nkd->nd", bary, tris[tri_index]),
            normals=mesh.face_normals()[tri_index],
            colors=mesh.sample_colors(tri_index, bary),
            tri_index=tri_index,
            barycentric=bary,
        )
        return ctx.save(points, "points.npz")


class ShortDistributor(PointDistributor[RandomParams]):
    """Breaks the contract: returns one point too few."""

    name = "short"
    Params = RandomParams

    def distribute(
        self, ctx: RunContext, source: MeshRef, n: int, params: RandomParams
    ) -> PointSetRef:
        mesh = source.load()
        m = n - 1
        points = PointSet(
            positions=mesh.vertices[mesh.faces[:m, 0]],
            normals=mesh.face_normals()[:m],
            colors=np.ones((m, 3)),
            tri_index=np.arange(m),
            barycentric=np.tile([1.0, 0.0, 0.0], (m, 1)),
        )
        return ctx.save(points, "points.npz")


class GreedyDistributor(PointDistributor[RandomParams]):
    """Always runs out of memory."""

    name = "greedy"
    Params = RandomParams

    def distribute(
        self, ctx: RunContext, source: MeshRef, n: int, params: RandomParams
    ) -> PointSetRef:
        raise ResourceExhausted("out of VRAM", data={"hint": "switch to the cloud provider"})


class ShiftParams(BaseModel):
    dz: float = Field(1.0, description="Lift at full progress")


class Shift(AnimationPrimitive[ShiftParams]):
    """Lifts the region."""

    name = "shift"
    Params = ShiftParams

    def evaluate(
        self,
        points: npt.NDArray[np.float64],
        mask: npt.NDArray[np.bool_],
        t: npt.NDArray[np.float64],
        progress: npt.NDArray[np.float64],
        params: ShiftParams,
    ) -> npt.NDArray[np.float64]:
        out = points.copy()
        out[:, mask, 2] += params.dz * progress[:, None]
        return out


plugin = Plugin(
    components=[RandomDistributor(), ShortDistributor(), GreedyDistributor(), Shift()],
    health_check=lambda: Health.ok("fixture ready"),
)

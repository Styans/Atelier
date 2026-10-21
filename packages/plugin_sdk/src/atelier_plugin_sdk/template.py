"""Skeleton of a new plugin (``atelier plugin new``)."""

from __future__ import annotations

import re
from pathlib import Path

from atelier_plugin_sdk.manifest import API_VERSION, MANIFEST_NAME
from atelier_plugin_sdk.roles import RoleName

_MANIFEST = """\
id = "{plugin_id}"
name = "{title}"
version = "0.1.0"
api_version = "{api_version}"
namespace = "{namespace}"
roles = ["{role}"]
runtime = "inprocess"
entry = "{plugin_id}:plugin"
requires_gpu = false
"""

_PYPROJECT = """\
[project]
name = "{dist_name}"
version = "0.1.0"
description = "{title}: an Atelier {role} plugin"
requires-python = ">=3.12"
dependencies = [
    "atelier-plugin-sdk",
    "numpy>=2.1",
    "pydantic>=2.9",
]

[build-system]
requires = ["uv_build>=0.13.4,<0.14"]
build-backend = "uv_build"
"""

_TEST = """\
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atelier_plugin_sdk.testing import LocalInvoker, run_contract_suite  # noqa: E402
from {plugin_id} import plugin  # noqa: E402


def test_components_follow_the_contract(tmp_path: Path) -> None:
    for component in plugin.components:
        results = run_contract_suite(LocalInvoker(component), tmp_path / component.name)
        failed = [r for r in results if not r.ok and r.level == "error"]
        assert not failed, failed
"""

_SOURCES: dict[str, str] = {
    "point_distributor": '''\
"""{title}: uniform random points on a mesh surface (a baseline to compare against)."""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel, Field

from atelier_plugin_sdk import (
    Health,
    MeshRef,
    Plugin,
    PointDistributor,
    PointSet,
    PointSetRef,
    RunContext,
)


class RandomParams(BaseModel):
    area_weighted: bool = Field(True, description="Pick triangles proportionally to their area")


class RandomDistributor(PointDistributor[RandomParams]):
    """Uniform random surface sampling: a baseline to compare against Pointille."""

    name = "random"
    version = "0.1.0"
    Params = RandomParams

    def distribute(
        self, ctx: RunContext, source: MeshRef, n: int, params: RandomParams
    ) -> PointSetRef:
        mesh = source.load()
        tris = mesh.triangles()
        area = mesh.face_areas()
        weights = area if params.area_weighted else np.ones_like(area)
        rng = ctx.rng()
        tri_index = rng.choice(len(tris), size=n, p=weights / weights.sum())
        r1, r2 = np.sqrt(rng.random(n)), rng.random(n)
        bary = np.stack([1 - r1, r1 * (1 - r2), r1 * r2], axis=1)
        positions = np.einsum("nk,nkd->nd", bary, tris[tri_index])
        ctx.progress(1.0, f"sampled {{n}} points")
        points = PointSet(
            positions=positions,
            normals=mesh.face_normals()[tri_index],
            colors=mesh.sample_colors(tri_index, bary),
            tri_index=tri_index.astype(np.int32),
            barycentric=bary,
        )
        return ctx.save(points, "points.npz")


plugin = Plugin(components=[RandomDistributor()], health_check=Health.ok)
''',
    "animation_primitive": '''\
"""{title}: an animation primitive."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field

from atelier_plugin_sdk import AnimationPrimitive, Health, Plugin


class LiftParams(BaseModel):
    height_m: float = Field(2.0, description="How far the region rises at full progress")


class Lift(AnimationPrimitive[LiftParams]):
    """Raises the region along Z with the track progress."""

    name = "lift"
    version = "0.1.0"
    Params = LiftParams

    def evaluate(
        self,
        points: npt.NDArray[np.float64],
        mask: npt.NDArray[np.bool_],
        t: npt.NDArray[np.float64],
        progress: npt.NDArray[np.float64],
        params: LiftParams,
    ) -> npt.NDArray[np.float64]:
        out = points.copy()
        out[:, mask, 2] += params.height_m * progress[:, None]
        return out


plugin = Plugin(components=[Lift()], health_check=Health.ok)
''',
    "validator_rule": '''\
"""{title}: a validator rule."""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel, Field

from atelier_domain import FleetSpec
from atelier_plugin_sdk import (
    CompiledShowRef,
    Health,
    Plugin,
    RuleResult,
    RunContext,
    ValidatorRule,
    Violation,
)


class FloorParams(BaseModel):
    floor_m: float = Field(1.0, description="Drones in a hold must stay above this height")


class Floor(ValidatorRule[FloorParams]):
    """Flags drones below a floor height during holds."""

    name = "floor"
    version = "0.1.0"
    Params = FloorParams

    def check(
        self, ctx: RunContext, show: CompiledShowRef, fleet: FleetSpec, params: FloorParams
    ) -> RuleResult:
        compiled = show.load()
        violations = []
        for mark in compiled.marks:
            if mark.kind != "hold":
                continue
            z = compiled.positions[mark.start_frame : mark.end_frame + 1, :, 2]
            low = np.nonzero((z < params.floor_m).any(axis=0))[0]
            for drone in low:
                violations.append(
                    Violation(
                        rule="floor",
                        frame_start=mark.start_frame,
                        frame_end=mark.end_frame,
                        t_start_s=mark.start_frame / compiled.fps,
                        t_end_s=mark.end_frame / compiled.fps,
                        drones=[int(drone)],
                        value=float(z[:, drone].min()),
                        limit=params.floor_m,
                        unit="m",
                        message="drone below the floor during a hold",
                    )
                )
        return RuleResult(violations=violations)


plugin = Plugin(components=[Floor()], health_check=Health.ok)
''',
    "exporter": '''\
"""{title}: an exporter."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from atelier_plugin_sdk import CompiledShowRef, Exporter, ExportResult, Health, Plugin, RunContext


class SummaryParams(BaseModel):
    pass


class Summary(Exporter[SummaryParams]):
    """Writes a JSON summary of the compiled show."""

    name = "summary"
    version = "0.1.0"
    Params = SummaryParams

    def export(
        self, ctx: RunContext, show: CompiledShowRef, out_dir: Path, params: SummaryParams
    ) -> ExportResult:
        compiled = show.load()
        path = out_dir / "summary.json"
        summary = {{
            "drones": compiled.drone_count,
            "frames": compiled.frame_count,
            "fps": compiled.fps,
            "duration_s": compiled.duration_s,
        }}
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return ExportResult(files=[path])


plugin = Plugin(components=[Summary()], health_check=Health.ok)
''',
}

TEMPLATE_ROLES: tuple[RoleName, ...] = (
    "point_distributor",
    "animation_primitive",
    "validator_rule",
    "exporter",
)


def create_plugin(
    plugins_dir: Path, plugin_id: str, role: RoleName, namespace: str | None = None
) -> Path:
    """Write a working plugin skeleton to ``plugins_dir/plugin_id`` and return its folder."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", plugin_id):
        raise ValueError("plugin id must be lower_snake_case (letters, digits, underscores)")
    if role not in _SOURCES:
        raise ValueError(f"no template for role {role!r}; available: {', '.join(TEMPLATE_ROLES)}")
    root = plugins_dir / plugin_id
    if root.exists():
        raise FileExistsError(f"{root} already exists")
    title = plugin_id.replace("_", " ").title()
    values = {
        "plugin_id": plugin_id,
        "title": title,
        "role": role,
        "namespace": namespace or plugin_id,
        "api_version": API_VERSION,
        "dist_name": plugin_id.replace("_", "-"),
    }
    files = {
        root / MANIFEST_NAME: _MANIFEST.format(**values),
        root / "pyproject.toml": _PYPROJECT.format(**values),
        root / "src" / plugin_id / "__init__.py": _SOURCES[role].format(**values),
        root / "tests" / f"test_{plugin_id}.py": _TEST.format(**values),
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root

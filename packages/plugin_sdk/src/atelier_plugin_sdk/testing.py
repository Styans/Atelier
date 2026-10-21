"""Contract tests for plugin components, on reference data shipped with the SDK.

``run_contract_suite`` works against any ``Invoker``: ``LocalInvoker`` here runs a component
in-process; the plugin host provides one that goes through the subprocess protocol.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np

from atelier_domain import FleetSpec, fleet_from_preset
from atelier_plugin_sdk.artifacts import (
    CompiledShow,
    CompiledShowRef,
    MeshData,
    MeshRef,
    PointSetRef,
    SceneMark,
)
from atelier_plugin_sdk.context import RunContext
from atelier_plugin_sdk.dispatch import invoke, normalize_params
from atelier_plugin_sdk.errors import PluginError
from atelier_plugin_sdk.results import ExportResult, RuleResult, TransitionPlan
from atelier_plugin_sdk.roles import Component, RoleName


class Invoker(Protocol):
    @property
    def role(self) -> RoleName: ...

    def invoke(
        self, args: Mapping[str, Any], params: Mapping[str, Any], *, seed: int, workdir: Path
    ) -> Any: ...

    def normalize_params(self, params: Mapping[str, Any]) -> dict[str, Any]: ...


class LocalInvoker:
    """Runs a component in this process, through the same dispatch as the host."""

    def __init__(self, component: Component[Any], config: Mapping[str, str] | None = None) -> None:
        self.component = component
        self.config = dict(config or {})

    @property
    def role(self) -> RoleName:
        return self.component.role

    def invoke(
        self, args: Mapping[str, Any], params: Mapping[str, Any], *, seed: int, workdir: Path
    ) -> Any:
        ctx = RunContext(seed=seed, workdir=workdir, config=self.config)
        return invoke(self.component, args, params, ctx)

    def normalize_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        return normalize_params(self.component, params)


@dataclass
class CheckResult:
    name: str
    ok: bool
    level: Literal["error", "warning"] = "error"
    detail: str = ""


# --- reference data -------------------------------------------------------------------------


def _midpoint(
    vertices: list[np.ndarray[Any, Any]], cache: dict[tuple[int, int], int], a: int, b: int
) -> int:
    key = (min(a, b), max(a, b))
    if key not in cache:
        v = vertices[a] + vertices[b]
        vertices.append(v / np.linalg.norm(v))
        cache[key] = len(vertices) - 1
    return cache[key]


def reference_mesh(subdivisions: int = 2) -> MeshData:
    """A unit sphere (subdivided octahedron) with a colour gradient in its vertex colours."""
    corners = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    vertices = [np.array(v, dtype=np.float64) for v in corners]
    faces = [(0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4), (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)]
    for _ in range(subdivisions):
        cache: dict[tuple[int, int], int] = {}
        new_faces = []
        for a, b, c in faces:
            ab = _midpoint(vertices, cache, a, b)
            bc = _midpoint(vertices, cache, b, c)
            ca = _midpoint(vertices, cache, c, a)
            new_faces += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = new_faces
    v = np.array(vertices)
    colors = ((v + 1.0) / 2.0).astype(np.float32)
    return MeshData(vertices=v, faces=np.array(faces), vertex_colors=colors)


def reference_compiled_show(drones: int = 9, frames: int = 61, fps: int = 30) -> CompiledShow:
    """A calm show: a grid 4 m apart climbing slowly from 10 m to 12 m."""
    side = int(np.ceil(np.sqrt(drones)))
    grid = np.array([(4.0 * (i % side), 4.0 * (i // side), 10.0) for i in range(drones)])
    rise = np.linspace(0.0, 2.0, frames)[:, None]
    positions = np.repeat(grid[None], frames, axis=0)
    positions[:, :, 2] += rise
    colors = np.full((frames, drones, 3), 200, dtype=np.uint8)
    marks = [
        SceneMark(
            kind="hold", scene_id="s_ref", title="Reference", start_frame=0, end_frame=frames - 1
        )
    ]
    return CompiledShow(positions=positions, colors=colors, fps=fps, marks=marks)


def reference_fleet(drones: int) -> FleetSpec:
    return fleet_from_preset("conservative", drones)


# --- suites ---------------------------------------------------------------------------------


def _attempt(name: str, fn: Any) -> tuple[CheckResult, Any]:
    try:
        return CheckResult(name, True), fn()
    except PluginError as exc:
        return CheckResult(name, False, detail=f"{type(exc).__name__}: {exc.message}"), None
    except Exception as exc:
        return CheckResult(name, False, detail=f"{type(exc).__name__}: {exc}"), None


def _defaults(invoker: Invoker) -> CheckResult:
    try:
        invoker.normalize_params({})
        return CheckResult("params have defaults", True, level="warning")
    except PluginError as exc:
        return CheckResult("params have defaults", False, "warning", exc.message)


def _point_distributor(invoker: Invoker, workdir: Path) -> list[CheckResult]:
    mesh = reference_mesh()
    source = MeshRef(path=mesh.save(workdir / "reference_mesh.npz"))
    n = 37
    results = []
    check, first = _attempt(
        f"distributes exactly {n} points",
        lambda: invoker.invoke({"source": source, "n": n}, {}, seed=1, workdir=workdir / "a"),
    )
    results.append(check)
    if not isinstance(first, PointSetRef):
        return results
    points = first.load()
    on_surface = np.einsum("nk,nkd->nd", points.barycentric, mesh.triangles()[points.tri_index])
    error = float(np.abs(on_surface - points.positions).max())
    results.append(
        CheckResult("points lie on their triangles", error < 1e-6, detail=f"max error {error:.2e}")
    )
    _, second = _attempt(
        "repeat",
        lambda: invoker.invoke({"source": source, "n": n}, {}, seed=1, workdir=workdir / "b"),
    )
    same = isinstance(second, PointSetRef) and second.load().content_hash() == points.content_hash()
    results.append(CheckResult("deterministic for a seed", same))
    return results


def _animation_primitive(invoker: Invoker, workdir: Path) -> list[CheckResult]:
    rest = reference_mesh().vertices[:50]
    frames = 12
    args = {
        "points": np.repeat(rest[None], frames, axis=0),
        "mask": rest[:, 0] > 0,
        "t": np.linspace(0.0, 2.0, frames),
        "progress": np.linspace(0.0, 1.0, frames),
    }
    check, first = _attempt(
        "evaluates all frames", lambda: invoker.invoke(args, {}, seed=1, workdir=workdir)
    )
    results = [check]
    if first is not None:
        second = invoker.invoke(args, {}, seed=1, workdir=workdir)
        results.append(CheckResult("deterministic", bool(np.array_equal(first, second))))
        start_ok = bool(np.allclose(first[0], rest, atol=1e-9))
        results.append(CheckResult("identity at progress 0", start_ok, level="warning"))
    return results


def _transition_planner(invoker: Invoker, workdir: Path) -> list[CheckResult]:
    d = 16
    start = np.array([(3.0 * (i % 4), 3.0 * (i // 4), 6.0) for i in range(d)])
    angles = np.linspace(0, 2 * np.pi, d, endpoint=False)
    targets = np.column_stack([10 * np.cos(angles), 10 * np.sin(angles), np.full(d, 15.0)])[::-1]
    fleet = reference_fleet(d)
    args = {"start": start, "targets": targets, "fleet": fleet, "fps": 30, "duration_s": None}
    check, plan = _attempt(
        "plans a transition", lambda: invoker.invoke(args, {}, seed=1, workdir=workdir)
    )
    results = [check]
    if isinstance(plan, TransitionPlan):
        again = invoker.invoke(args, {}, seed=1, workdir=workdir)
        same = bool(np.array_equal(plan.positions, again.positions))
        results.append(CheckResult("deterministic for a seed", same))
        v = np.diff(plan.positions, axis=0) * 30
        h = float(np.linalg.norm(v[..., :2], axis=2).max())
        results.append(
            CheckResult(
                "respects horizontal speed when duration is free",
                h <= fleet.max_speed_h_ms * 1.001,
                level="warning",
                detail=f"max {h:.2f} m/s",
            )
        )
    return results


def _validator_rule(invoker: Invoker, workdir: Path) -> list[CheckResult]:
    show = reference_compiled_show()
    ref = show.save(workdir / "reference_show.npz")
    args = {"show": CompiledShowRef(path=ref), "fleet": reference_fleet(show.drone_count)}
    check, result = _attempt(
        "checks a compiled show", lambda: invoker.invoke(args, {}, seed=1, workdir=workdir)
    )
    results = [check]
    if isinstance(result, RuleResult):
        clean = not result.violations
        results.append(
            CheckResult(
                "no violations on a calm show",
                clean,
                level="warning",
                detail=f"{len(result.violations)} violations",
            )
        )
    return results


def _exporter(invoker: Invoker, workdir: Path) -> list[CheckResult]:
    show = reference_compiled_show()
    ref = CompiledShowRef(path=show.save(workdir / "reference_show.npz"))
    out_dir = workdir / "export"
    out_dir.mkdir(parents=True, exist_ok=True)
    check, result = _attempt(
        "exports files",
        lambda: invoker.invoke({"show": ref, "out_dir": out_dir}, {}, seed=1, workdir=workdir),
    )
    results = [check]
    if isinstance(result, ExportResult):
        results.append(CheckResult("returns at least one file", bool(result.files)))
    return results


SUITES = {
    "point_distributor": _point_distributor,
    "animation_primitive": _animation_primitive,
    "transition_planner": _transition_planner,
    "validator_rule": _validator_rule,
    "exporter": _exporter,
}


def run_contract_suite(invoker: Invoker, workdir: Path) -> list[CheckResult]:
    """Check a component against the contract of its role; errors fail, warnings inform."""
    workdir.mkdir(parents=True, exist_ok=True)
    suite = SUITES.get(invoker.role)
    if suite is None:
        return [
            CheckResult(f"no contract suite for role {invoker.role} yet", True, level="warning")
        ]
    return [_defaults(invoker), *suite(invoker, workdir)]

"""Output checks every call goes through; a failure is a ``ContractViolation``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

from atelier_plugin_sdk.artifacts import ArtifactRef, MeshRef, PointSetRef
from atelier_plugin_sdk.errors import ContractViolation
from atelier_plugin_sdk.results import ExportResult, RuleResult, TransitionPlan
from atelier_plugin_sdk.roles import RoleName


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractViolation(message)


def _point_distributor(args: Mapping[str, Any], result: Any) -> None:
    _require(isinstance(result, PointSetRef), "point_distributor must return a PointSetRef")
    points = result.load()
    n = int(args["n"])
    _require(len(points) == n, f"point_distributor returned {len(points)} points, expected {n}")
    _require(bool(np.isfinite(points.positions).all()), "point positions must be finite")
    source = args["source"]
    if isinstance(source, MeshRef):
        faces = len(source.load().faces)
        _require(
            bool((points.tri_index >= 0).all() and (points.tri_index < faces).all()),
            "tri_index out of range of the source mesh",
        )


def _kernel(args: Mapping[str, Any], result: Any, expected_shape: tuple[int, ...]) -> None:
    _require(isinstance(result, np.ndarray), "kernel must return a numpy array")
    _require(
        result.shape == expected_shape, f"kernel returned {result.shape}, expected {expected_shape}"
    )
    _require(bool(np.isfinite(result).all()), "kernel output must be finite")


def _animation_primitive(args: Mapping[str, Any], result: Any) -> None:
    points = np.asarray(args["points"])
    _kernel(args, result, points.shape)
    mask = np.asarray(args["mask"], dtype=bool)
    _require(
        bool(np.allclose(result[:, ~mask], points[:, ~mask])),
        "animation_primitive moved points outside its region",
    )


def _color_effect(args: Mapping[str, Any], result: Any) -> None:
    t = np.asarray(args["t"])
    base = np.asarray(args["base"])
    _kernel(args, result, (len(t), *base.shape))


def _transition_planner(args: Mapping[str, Any], result: Any) -> None:
    _require(isinstance(result, TransitionPlan), "transition_planner must return a TransitionPlan")
    start = np.asarray(args["start"], dtype=np.float64)
    targets = np.asarray(args["targets"], dtype=np.float64)
    d = len(start)
    assignment = np.asarray(result.assignment)
    _require(
        assignment.shape == (d,) and bool(np.array_equal(np.sort(assignment), np.arange(d))),
        "assignment must be a permutation of the targets",
    )
    positions = np.asarray(result.positions)
    _require(positions.ndim == 3 and positions.shape[1:] == (d, 3), "positions must be (F, D, 3)")
    _require(len(positions) >= 2, "a transition needs at least two frames")
    _require(bool(np.isfinite(positions).all()), "transition positions must be finite")
    _require(bool(np.allclose(positions[0], start, atol=1e-6)), "transition must begin at start")
    _require(
        bool(np.allclose(positions[-1], targets[assignment], atol=1e-6)),
        "transition must end at the assigned targets",
    )
    fps = int(args["fps"])
    _require(
        abs(result.duration_s - (len(positions) - 1) / fps) < 1e-6,
        "duration_s must equal (frames - 1) / fps",
    )


def _validator_rule(args: Mapping[str, Any], result: Any) -> None:
    _require(isinstance(result, RuleResult), "validator_rule must return a RuleResult")


def _exporter(args: Mapping[str, Any], result: Any) -> None:
    _require(isinstance(result, ExportResult), "exporter must return an ExportResult")
    missing = [str(f) for f in result.files if not f.exists()]
    _require(not missing, f"exported files do not exist: {missing}")


def _artifact(args: Mapping[str, Any], result: Any) -> None:
    _require(isinstance(result, ArtifactRef), "must return an artifact reference")
    _require(result.path.exists(), f"returned artifact does not exist: {result.path}")


CHECKS: dict[RoleName, Callable[[Mapping[str, Any], Any], None]] = {
    "image_generator": _artifact,
    "mesh_generator": _artifact,
    "mesh_processor": _artifact,
    "formation_source": _artifact,
    "point_distributor": _point_distributor,
    "animation_primitive": _animation_primitive,
    "color_effect": _color_effect,
    "transition_planner": _transition_planner,
    "validator_rule": _validator_rule,
    "exporter": _exporter,
}


def check_output(role: RoleName, args: Mapping[str, Any], result: Any) -> None:
    CHECKS[role](args, result)

"""Roles: the typed contracts a component can implement (contract v1, docs/ARCHITECTURE.md)."""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Generic, Literal, TypeVar

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

from atelier_domain import FleetSpec
from atelier_plugin_sdk.artifacts import (
    CompiledShowRef,
    ContourRef,
    ImageRef,
    ImageSetRef,
    MeshRef,
    PointSetRef,
)
from atelier_plugin_sdk.context import RunContext
from atelier_plugin_sdk.results import ExportResult, RuleResult, TransitionPlan

RoleName = Literal[
    "image_generator",
    "mesh_generator",
    "mesh_processor",
    "formation_source",
    "point_distributor",
    "animation_primitive",
    "color_effect",
    "transition_planner",
    "validator_rule",
    "exporter",
]
ROLE_NAMES: tuple[RoleName, ...] = (
    "image_generator",
    "mesh_generator",
    "mesh_processor",
    "formation_source",
    "point_distributor",
    "animation_primitive",
    "color_effect",
    "transition_planner",
    "validator_rule",
    "exporter",
)

P = TypeVar("P", bound=BaseModel)
Array = npt.NDArray[np.float64]


class NoParams(BaseModel):
    """For components without parameters."""


class Component(ABC, Generic[P]):
    """Base of every component. Subclasses set ``role`` (via the role class), ``name``,
    ``version`` and ``Params``; the docstring becomes the description shown in the UI."""

    role: ClassVar[RoleName]
    name: ClassVar[str]
    version: ClassVar[str] = "0.1.0"
    Params: ClassVar[type[BaseModel]] = NoParams

    @classmethod
    def description(cls) -> str:
        doc = inspect.getdoc(cls) or ""
        return doc.split("\n\n")[0].replace("\n", " ")


class ImageGenerator(Component[P]):
    role: ClassVar[RoleName] = "image_generator"

    @abstractmethod
    def generate(
        self, ctx: RunContext, prompt: str, negative: str | None, n: int, params: P
    ) -> ImageSetRef: ...


class MeshGenerator(Component[P]):
    role: ClassVar[RoleName] = "mesh_generator"

    @abstractmethod
    def generate(self, ctx: RunContext, source: ImageRef | str, params: P) -> MeshRef: ...


class MeshProcessor(Component[P]):
    role: ClassVar[RoleName] = "mesh_processor"

    @abstractmethod
    def process(self, ctx: RunContext, mesh: MeshRef, params: P) -> MeshRef: ...


class FormationSource(Component[P]):
    role: ClassVar[RoleName] = "formation_source"

    @abstractmethod
    def build(self, ctx: RunContext, source: str | Path, params: P) -> MeshRef | ContourRef: ...


class PointDistributor(Component[P]):
    """Places exactly ``n`` points on a mesh (or contour)."""

    role: ClassVar[RoleName] = "point_distributor"

    @abstractmethod
    def distribute(
        self, ctx: RunContext, source: MeshRef | ContourRef, n: int, params: P
    ) -> PointSetRef: ...


class AnimationPrimitive(Component[P]):
    """A pure motion kernel, called once per track for all its frames.

    ``points[T, N, 3]`` are the figure-local positions so far (centred, scaled), ``mask[N]``
    selects the region, ``t[T]`` are seconds since the track start (clipped to its duration)
    and ``progress[T]`` is the eased progress in [0, 1]. Returns new ``points[T, N, 3]``;
    points outside the mask must not move.
    """

    role: ClassVar[RoleName] = "animation_primitive"

    @abstractmethod
    def evaluate(
        self,
        points: Array,
        mask: npt.NDArray[np.bool_],
        t: Array,
        progress: Array,
        params: P,
    ) -> Array: ...


class ColorEffect(Component[P]):
    """A colour kernel: ``base[N, 3]`` linear colours -> ``colors[T, N, 3]``."""

    role: ClassVar[RoleName] = "color_effect"

    @abstractmethod
    def evaluate(self, rest: Array, base: Array, t: Array, progress: Array, params: P) -> Array: ...


class TransitionPlanner(Component[P]):
    """Assigns drones to targets and flies them there.

    ``duration_s`` is the requested duration, or None for the shortest safe one.
    """

    role: ClassVar[RoleName] = "transition_planner"

    @abstractmethod
    def plan(
        self,
        ctx: RunContext,
        start: Array,
        targets: Array,
        fleet: FleetSpec,
        fps: int,
        duration_s: float | None,
        params: P,
    ) -> TransitionPlan: ...


class ValidatorRule(Component[P]):
    role: ClassVar[RoleName] = "validator_rule"

    @abstractmethod
    def check(
        self, ctx: RunContext, show: CompiledShowRef, fleet: FleetSpec, params: P
    ) -> RuleResult: ...


class Exporter(Component[P]):
    role: ClassVar[RoleName] = "exporter"

    @abstractmethod
    def export(
        self, ctx: RunContext, show: CompiledShowRef, out_dir: Path, params: P
    ) -> ExportResult: ...


@dataclass(frozen=True)
class RoleSpec:
    """How the host calls a role: method name, whether it takes a ``RunContext``, and the
    names of the positional arguments between ``ctx`` and ``params``."""

    method: str
    takes_ctx: bool
    args: tuple[str, ...]
    base: type[Component[BaseModel]]


ROLE_SPECS: dict[RoleName, RoleSpec] = {
    "image_generator": RoleSpec("generate", True, ("prompt", "negative", "n"), ImageGenerator),
    "mesh_generator": RoleSpec("generate", True, ("source",), MeshGenerator),
    "mesh_processor": RoleSpec("process", True, ("mesh",), MeshProcessor),
    "formation_source": RoleSpec("build", True, ("source",), FormationSource),
    "point_distributor": RoleSpec("distribute", True, ("source", "n"), PointDistributor),
    "animation_primitive": RoleSpec(
        "evaluate", False, ("points", "mask", "t", "progress"), AnimationPrimitive
    ),
    "color_effect": RoleSpec("evaluate", False, ("rest", "base", "t", "progress"), ColorEffect),
    "transition_planner": RoleSpec(
        "plan", True, ("start", "targets", "fleet", "fps", "duration_s"), TransitionPlanner
    ),
    "validator_rule": RoleSpec("check", True, ("show", "fleet"), ValidatorRule),
    "exporter": RoleSpec("export", True, ("show", "out_dir"), Exporter),
}

"""The show: the single source of truth stored in ``show.json``."""

from typing import Literal, Self

from pydantic import Field, model_validator

from atelier_domain.artifacts import ArtifactRecord
from atelier_domain.assets import AudioTrack
from atelier_domain.base import DomainModel, HexColor, Vec3
from atelier_domain.brief import Brief
from atelier_domain.model_card import ModelCard
from atelier_domain.pipeline import PipelineConfig
from atelier_domain.scene import Scene

SCHEMA_VERSION: Literal[1] = 1

StageName = Literal["brief", "program", "models", "points", "animation", "validation", "export"]
StageStatus = Literal["todo", "in_progress", "approved"]


class FlightSettings(DomainModel):
    """Takeoff, landing and default placement of the figures."""

    takeoff_altitude_m: float = Field(6.0, gt=0, description="Height of the vertical takeoff")
    takeoff_speed_ms: float | None = Field(None, gt=0, description="None = 80% of max climb")
    landing_speed_ms: float | None = Field(None, gt=0, description="None = 80% of max descent")
    grid_center_m: Vec3 = Field((0.0, 0.0, 0.0), description="Centre of the ground grid")
    formation_base_altitude_m: float = Field(
        15.0, gt=0, description="Default height of the bottom of each figure"
    )
    formation_spacing_factor: float = Field(
        1.4142135623730951,
        ge=1.0,
        description="Figure spacing = factor x min distance when its size is automatic (ADR-008)",
    )
    idle_color: HexColor = Field("#202020", description="LED colour on the ground and in takeoff")


class ShowOutputs(DomainModel):
    """Current results of the later stages, by artifact id."""

    compiled: str | None = None
    validation: str | None = None
    exports: dict[str, str] = Field(default_factory=dict, description="Exporter -> artifact id")


class Show(DomainModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    id: str
    revision: int = Field(0, ge=0)
    seed: int = 0
    fps: int = Field(30, gt=0)
    brief: Brief
    models: dict[str, ModelCard] = Field(default_factory=dict)
    program: list[Scene] = Field(default_factory=list)
    pipeline: PipelineConfig
    flight: FlightSettings = Field(default_factory=FlightSettings)
    audio: AudioTrack | None = None
    artifacts: dict[str, ArtifactRecord] = Field(default_factory=dict)
    outputs: ShowOutputs = Field(default_factory=ShowOutputs)
    stage_status: dict[StageName, StageStatus] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        scene_ids = [scene.id for scene in self.program]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("scene ids must be unique")
        for scene in self.program:
            if scene.model_id not in self.models:
                raise ValueError(f"scene {scene.id!r} refers to unknown model {scene.model_id!r}")
        for model_id, card in self.models.items():
            if card.id != model_id:
                raise ValueError(f"model key {model_id!r} does not match its id {card.id!r}")
        return self

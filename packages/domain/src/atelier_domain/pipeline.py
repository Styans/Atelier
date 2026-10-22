"""Which plugin component runs each stage, and with which parameters."""

from typing import Any

from pydantic import Field

from atelier_domain.base import DomainModel


class StageConfig(DomainModel):
    component: str = Field(description="Component id, e.g. 'pointille' or 'builtin.min_distance'")
    params: dict[str, Any] = Field(default_factory=dict)


class PipelineConfig(DomainModel):
    point_distributor: StageConfig
    transition_planner: StageConfig
    validators: list[StageConfig] = Field(default_factory=list)
    exporters: list[StageConfig] = Field(default_factory=list)
    image_generator: StageConfig | None = Field(None, description="Concept images")
    mesh_generator: StageConfig | None = Field(None, description="Image -> 3D model")

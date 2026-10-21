"""Model cards: one figure of the show and the artifacts produced for it."""

from typing import Literal

from pydantic import Field

from atelier_domain.base import DomainModel, HexColor
from atelier_domain.pipeline import StageConfig

ModelKind = Literal["mesh3d", "text", "svg", "primitive"]
Facing = Literal["front", "top", "free"]
ColorSource = Literal["texture", "palette", "solid"]
ModelStatus = Literal["draft", "approved", "generating", "ready", "failed"]


class ModelAssets(DomainModel):
    """Current artifacts of a model, by artifact id (see ``Show.artifacts``)."""

    concepts: list[str] = Field(default_factory=list)
    selected_concept: str | None = None
    mesh: str | None = None
    point_sets: dict[int, str] = Field(
        default_factory=dict, description="Drone count -> point_set artifact id"
    )


class ModelCard(DomainModel):
    id: str
    name: str
    kind: ModelKind = "mesh3d"
    description: str = ""
    style: str | None = None
    image_prompt: str | None = None
    negative_prompt: str | None = None
    palette: list[HexColor] = Field(default_factory=list)
    target_height_m: float | None = Field(
        None, gt=0, description="None = smallest size that keeps the drones far enough apart"
    )
    facing: Facing = "front"
    drone_count: int | None = Field(None, gt=0, description="None = the whole fleet")
    color_source: ColorSource = "texture"
    status: ModelStatus = "draft"
    assets: ModelAssets = Field(default_factory=ModelAssets)
    pipeline_overrides: dict[str, StageConfig] = Field(
        default_factory=dict, description="Role -> stage config used for this model only"
    )
    version: int = 1

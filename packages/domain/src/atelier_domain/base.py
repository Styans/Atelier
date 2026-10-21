"""Shared base model and small value types."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Vec3 = tuple[float, float, float]
"""A 3D vector in show coordinates: Z-up, meters, right-handed."""

HexColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
"""An sRGB colour such as ``#ffcc00``."""


class DomainModel(BaseModel):
    """Base for every schema stored in ``show.json``: unknown fields are errors."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

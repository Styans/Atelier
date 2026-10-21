"""The show brief: what the client wants."""

import datetime as dt
from typing import Literal

from pydantic import Field

from atelier_domain.base import DomainModel, HexColor
from atelier_domain.fleet import FleetSpec

ViewerDirection = Literal["+Y", "-Y", "+X", "-X"]
"""Side of the show the audience is on (ADR-009); the figures face it."""


class Brief(DomainModel):
    title: str = Field(min_length=1)
    occasion: str | None = None
    date: dt.date | None = None
    location: str | None = None
    viewer_direction: ViewerDirection = "-Y"
    duration_s: float = Field(gt=0, description="Time budget of the whole show")
    fleet: FleetSpec
    mood: str | None = None
    palette: list[HexColor] = Field(default_factory=list)
    must_have: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    notes: str | None = None
    status: Literal["draft", "approved"] = "draft"

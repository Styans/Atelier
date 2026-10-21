"""Fleet limits and the named presets they come from (ADR-007)."""

from collections.abc import Mapping
from typing import Self

from pydantic import Field, model_validator

from atelier_domain.base import DomainModel


class FleetLimits(DomainModel):
    """Physical and safety limits of a fleet, independent of its size."""

    min_distance_m: float = Field(gt=0, description="Minimum distance between any two drones")
    max_speed_h_ms: float = Field(gt=0, description="Maximum horizontal speed")
    max_speed_up_ms: float = Field(gt=0, description="Maximum climb speed")
    max_speed_down_ms: float = Field(gt=0, description="Maximum descent speed")
    max_accel_ms2: float = Field(gt=0, description="Maximum acceleration (3D)")
    altitude_min_m: float = Field(ge=0, description="Minimum altitude outside takeoff/landing")
    altitude_max_m: float = Field(gt=0, description="Maximum altitude")
    takeoff_grid_spacing_m: float = Field(gt=0, description="Spacing of the ground grid")

    @model_validator(mode="after")
    def _check_ranges(self) -> Self:
        if self.altitude_max_m <= self.altitude_min_m:
            raise ValueError("altitude_max_m must be above altitude_min_m")
        return self


class FleetSpec(FleetLimits):
    """The fleet of one show: its size, its limits and the preset they were taken from."""

    drone_count: int = Field(gt=0)
    preset: str | None = Field(None, description="Preset the limits were copied from, if any")


BUILTIN_FLEET_PRESETS: Mapping[str, FleetLimits] = {
    "conservative": FleetLimits(
        min_distance_m=2.0,
        max_speed_h_ms=4.0,
        max_speed_up_ms=2.0,
        max_speed_down_ms=1.5,
        max_accel_ms2=2.0,
        altitude_min_m=2.5,
        altitude_max_m=120.0,
        takeoff_grid_spacing_m=3.0,
    ),
    "skybrush_default": FleetLimits(
        min_distance_m=3.0,
        max_speed_h_ms=10.0,
        max_speed_up_ms=2.0,
        max_speed_down_ms=2.0,
        max_accel_ms2=4.0,
        altitude_min_m=2.5,
        altitude_max_m=150.0,
        takeoff_grid_spacing_m=3.0,
    ),
}


def fleet_from_preset(
    name: str,
    drone_count: int,
    presets: Mapping[str, FleetLimits] = BUILTIN_FLEET_PRESETS,
) -> FleetSpec:
    """Build a fleet of ``drone_count`` drones with the limits of preset ``name``."""
    try:
        limits = presets[name]
    except KeyError:
        known = ", ".join(sorted(presets))
        raise KeyError(f"unknown fleet preset {name!r}; known presets: {known}") from None
    return FleetSpec(**limits.model_dump(), drone_count=drone_count, preset=name)

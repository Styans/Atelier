"""Pydantic schemas of the show domain: brief, fleet, model cards, scenes, show."""

from importlib.metadata import version

from atelier_domain.animation import (
    AnimationSpec,
    AnimTrack,
    ColorTrack,
    Easing,
    Region,
    RegionAll,
    RegionBox,
    RegionPlaneSplit,
)
from atelier_domain.artifacts import (
    ArtifactKind,
    ArtifactRecord,
    Producer,
    canonical_json,
    derive_seed,
    fingerprint,
)
from atelier_domain.assets import AssetRef, AudioTrack
from atelier_domain.base import DomainModel, HexColor, Vec3
from atelier_domain.brief import Brief, ViewerDirection
from atelier_domain.fleet import BUILTIN_FLEET_PRESETS, FleetLimits, FleetSpec, fleet_from_preset
from atelier_domain.model_card import ModelAssets, ModelCard
from atelier_domain.pipeline import PipelineConfig, StageConfig
from atelier_domain.scene import Scene, TransitionSpec
from atelier_domain.show import SCHEMA_VERSION, FlightSettings, Show, ShowOutputs, StageName

__version__ = version("atelier-domain")

__all__ = [
    "BUILTIN_FLEET_PRESETS",
    "SCHEMA_VERSION",
    "AnimTrack",
    "AnimationSpec",
    "ArtifactKind",
    "ArtifactRecord",
    "AssetRef",
    "AudioTrack",
    "Brief",
    "ColorTrack",
    "DomainModel",
    "Easing",
    "FleetLimits",
    "FleetSpec",
    "FlightSettings",
    "HexColor",
    "ModelAssets",
    "ModelCard",
    "PipelineConfig",
    "Producer",
    "Region",
    "RegionAll",
    "RegionBox",
    "RegionPlaneSplit",
    "Scene",
    "Show",
    "ShowOutputs",
    "StageConfig",
    "StageName",
    "TransitionSpec",
    "Vec3",
    "ViewerDirection",
    "canonical_json",
    "derive_seed",
    "fingerprint",
    "fleet_from_preset",
]

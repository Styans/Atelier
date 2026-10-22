"""Plugin SDK: roles, artifact types, subprocess server, plugin template and contract tests."""

from importlib.metadata import PackageNotFoundError, version

from atelier_plugin_sdk.artifacts import (
    ArtifactRef,
    AudioInfo,
    CompiledShow,
    CompiledShowRef,
    ContourRef,
    ImageRef,
    ImageSetRef,
    MeshData,
    MeshRef,
    PointSet,
    PointSetRef,
    SceneMark,
    hex_to_linear,
    linear_to_srgb8,
    srgb_to_linear,
)
from atelier_plugin_sdk.context import RunContext
from atelier_plugin_sdk.errors import (
    Cancelled,
    ContractViolation,
    InvalidParams,
    PluginError,
    ResourceExhausted,
)
from atelier_plugin_sdk.manifest import API_VERSION, PluginManifest, load_manifest
from atelier_plugin_sdk.plugin import Health, Plugin
from atelier_plugin_sdk.results import ExportResult, RuleResult, TransitionPlan, Violation
from atelier_plugin_sdk.roles import (
    ROLE_NAMES,
    ROLE_SPECS,
    AnimationPrimitive,
    ColorEffect,
    Component,
    Exporter,
    FormationSource,
    ImageGenerator,
    MeshGenerator,
    MeshProcessor,
    NoParams,
    PointDistributor,
    RoleName,
    TransitionPlanner,
    ValidatorRule,
)

try:
    __version__ = version("atelier-plugin-sdk")
except PackageNotFoundError:  # imported from source, e.g. by a plugin in another venv
    __version__ = "0.0.0+source"

__all__ = [
    "API_VERSION",
    "ROLE_NAMES",
    "ROLE_SPECS",
    "AnimationPrimitive",
    "ArtifactRef",
    "AudioInfo",
    "Cancelled",
    "ColorEffect",
    "CompiledShow",
    "CompiledShowRef",
    "Component",
    "ContourRef",
    "ContractViolation",
    "ExportResult",
    "Exporter",
    "FormationSource",
    "Health",
    "ImageGenerator",
    "ImageRef",
    "ImageSetRef",
    "InvalidParams",
    "MeshData",
    "MeshGenerator",
    "MeshProcessor",
    "MeshRef",
    "NoParams",
    "Plugin",
    "PluginError",
    "PluginManifest",
    "PointDistributor",
    "PointSet",
    "PointSetRef",
    "ResourceExhausted",
    "RoleName",
    "RuleResult",
    "RunContext",
    "SceneMark",
    "TransitionPlan",
    "TransitionPlanner",
    "ValidatorRule",
    "Violation",
    "hex_to_linear",
    "linear_to_srgb8",
    "load_manifest",
    "srgb_to_linear",
]

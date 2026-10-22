"""Local configuration from ``atelier.toml`` (git-ignored; see ``atelier.example.toml``)."""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from atelier_domain import BUILTIN_FLEET_PRESETS, FleetLimits, PipelineConfig, StageConfig

CONFIG_NAME = "atelier.toml"
CONFIG_ENV = "ATELIER_CONFIG"

DEFAULT_PIPELINE = PipelineConfig(
    point_distributor=StageConfig(component="pointille"),
    transition_planner=StageConfig(component="builtin.hungarian_direct"),
    validators=[
        StageConfig(component="builtin.min_distance"),
        StageConfig(component="builtin.speed"),
        StageConfig(component="builtin.accel"),
        StageConfig(component="builtin.altitude"),
    ],
    exporters=[StageConfig(component="builtin.csv"), StageConfig(component="blender_skybrush")],
)


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathsConfig(_Section):
    projects_dir: Path = Path("projects")
    blender_exe: Path | None = None
    plugin_dirs: list[Path] = Field(default_factory=lambda: [Path("plugins")])


class BlenderConfig(_Section):
    skybrush_addon: str = "ui_skybrush_studio"
    timeout_s: float = Field(1800.0, gt=0)


class PluginsConfig(_Section):
    disabled: list[str] = Field(default_factory=list)


class AtelierConfig(_Section):
    root: Path = Field(description="Folder of atelier.toml; relative paths resolve against it")
    paths: PathsConfig = Field(default_factory=PathsConfig)
    blender: BlenderConfig = Field(default_factory=BlenderConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    fleet_presets: dict[str, FleetLimits] = Field(default_factory=dict)
    plugin_settings: dict[str, dict[str, str]] = Field(default_factory=dict)
    pipeline: PipelineConfig = Field(default_factory=lambda: DEFAULT_PIPELINE.model_copy(deep=True))

    def resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else (self.root / path).resolve()

    @property
    def projects_dir(self) -> Path:
        return self.resolve(self.paths.projects_dir)

    @property
    def plugin_dirs(self) -> list[Path]:
        return [self.resolve(p) for p in self.paths.plugin_dirs]

    @property
    def blender_exe(self) -> Path | None:
        return self.resolve(self.paths.blender_exe) if self.paths.blender_exe else None

    @property
    def cache_dir(self) -> Path:
        return self.root / ".atelier"

    @property
    def presets(self) -> Mapping[str, FleetLimits]:
        return {**BUILTIN_FLEET_PRESETS, **self.fleet_presets}

    @property
    def ui_settings_path(self) -> Path:
        return self.cache_dir / "ui_settings.json"

    def ui_settings(self) -> dict[str, Any]:
        """Choices made in the UI (e.g. disabled plugins); atelier.toml stays hand-written."""
        try:
            data: dict[str, Any] = json.loads(self.ui_settings_path.read_text(encoding="utf-8"))
            return data
        except (OSError, json.JSONDecodeError):
            return {}

    def save_ui_settings(self, values: dict[str, Any]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        merged = self.ui_settings() | values
        self.ui_settings_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    @property
    def disabled_plugins(self) -> list[str]:
        return sorted(
            set(self.plugins.disabled) | set(self.ui_settings().get("disabled_plugins", []))
        )

    def host_config(self) -> dict[str, str]:
        """Settings passed to plugins through ``RunContext.config``."""
        values = {
            "skybrush_addon": self.blender.skybrush_addon,
            "blender_timeout_s": str(self.blender.timeout_s),
        }
        if self.blender_exe is not None:
            values["blender_exe"] = str(self.blender_exe)
        return values


def find_config(start: Path | None = None) -> Path | None:
    if os.environ.get(CONFIG_ENV):
        return Path(os.environ[CONFIG_ENV])
    folder = (start or Path.cwd()).resolve()
    for candidate in [folder, *folder.parents]:
        if (candidate / CONFIG_NAME).is_file():
            return candidate / CONFIG_NAME
    return None


def load_config(path: Path | None = None, *, root: Path | None = None) -> AtelierConfig:
    """Read ``atelier.toml``; without one, defaults rooted at ``root`` (or the cwd)."""
    path = path or find_config()
    if path is None:
        return AtelierConfig(root=(root or Path.cwd()).resolve())
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return AtelierConfig.model_validate({**data, "root": path.resolve().parent})

"""``atelier-plugin.toml``: the manifest of a plugin."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from atelier_plugin_sdk.roles import RoleName

API_VERSION = "1"
MANIFEST_NAME = "atelier-plugin.toml"


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    version: str
    api_version: str
    namespace: str | None = Field(None, pattern=r"^[a-z][a-z0-9_]*$")
    roles: list[RoleName]
    runtime: Literal["inprocess", "subprocess"] = "inprocess"
    entry: str = Field(pattern=r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$", description="module:attribute")
    command: list[str] = Field(
        default_factory=list,
        description=(
            "Subprocess command, run in the plugin folder. Placeholders: {python} (the"
            " platform interpreter), {entry}, {plugin_dir}. Empty = run the SDK server with"
            " the platform interpreter."
        ),
    )
    requires_gpu: bool = False
    description: str = ""

    @property
    def effective_namespace(self) -> str:
        return self.namespace or self.id

    @property
    def entry_module(self) -> str:
        return self.entry.split(":")[0]


def load_manifest(path: Path) -> PluginManifest:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return PluginManifest.model_validate(data)

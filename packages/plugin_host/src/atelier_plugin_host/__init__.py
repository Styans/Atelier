"""Plugin host: discovery, registry, health checks, in-process and subprocess runners."""

from importlib.metadata import version

from atelier_plugin_host.registry import (
    ComponentHandle,
    ComponentInfo,
    ComponentNotFound,
    LoadedPlugin,
    PluginHostError,
    PluginRegistry,
)
from atelier_plugin_host.runners import InProcessRunner, SubprocessRunner

__version__ = version("atelier-plugin-host")

__all__ = [
    "ComponentHandle",
    "ComponentInfo",
    "ComponentNotFound",
    "InProcessRunner",
    "LoadedPlugin",
    "PluginHostError",
    "PluginRegistry",
    "SubprocessRunner",
]

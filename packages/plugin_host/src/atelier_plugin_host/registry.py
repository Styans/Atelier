"""Plugin discovery, the component registry and component handles."""

from __future__ import annotations

import copy
import importlib
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from importlib.resources import files
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from atelier_plugin_host.runners import (
    InProcessRunner,
    LogCallback,
    ProgressCallback,
    Runner,
    SubprocessRunner,
)
from atelier_plugin_sdk import (
    API_VERSION,
    Health,
    Plugin,
    PluginError,
    PluginManifest,
    load_manifest,
)
from atelier_plugin_sdk.contracts import check_output
from atelier_plugin_sdk.manifest import MANIFEST_NAME
from atelier_plugin_sdk.roles import ROLE_NAMES, RoleName

ENTRY_POINT_GROUP = "atelier.plugins"


class PluginHostError(Exception):
    pass


class ComponentNotFound(PluginHostError):
    pass


@dataclass(frozen=True)
class ComponentInfo:
    id: str
    plugin_id: str
    role: RoleName
    name: str
    version: str
    description: str
    params_schema: dict[str, Any]


@dataclass
class LoadedPlugin:
    manifest: PluginManifest
    root: Path
    status: Literal["ok", "degraded", "error", "disabled"] = "ok"
    error: str | None = None
    health: Health | None = None
    components: list[ComponentInfo] = field(default_factory=list)
    runner: Runner | None = None

    @property
    def usable(self) -> bool:
        return self.status in ("ok", "degraded") and self.runner is not None


class ComponentHandle:
    """A callable component; implements the SDK's ``Invoker`` protocol."""

    def __init__(
        self, info: ComponentInfo, plugin: LoadedPlugin, config: Mapping[str, str]
    ) -> None:
        self.info = info
        self.plugin = plugin
        self.config = config

    @property
    def role(self) -> RoleName:
        return self.info.role

    @property
    def runner(self) -> Runner:
        assert self.plugin.runner is not None
        return self.plugin.runner

    def normalize_params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        return self.runner.normalize_params(self.info.role, self.info.name, params)

    def invoke(
        self,
        args: Mapping[str, Any],
        params: Mapping[str, Any],
        *,
        seed: int,
        workdir: Path,
        on_progress: ProgressCallback | None = None,
        on_log: LogCallback | None = None,
    ) -> Any:
        result = self.runner.invoke(
            self.info.role,
            self.info.name,
            args,
            params,
            seed=seed,
            workdir=workdir,
            config=self.config,
            on_progress=on_progress,
            on_log=on_log,
        )
        if not isinstance(self.runner, InProcessRunner):
            check_output(self.info.role, args, result)  # never trust the other side
        return result


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve local ``$ref``s so a schema can be embedded in another one."""
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node and node["$ref"].startswith("#/$defs/"):
                target = copy.deepcopy(defs[node["$ref"].removeprefix("#/$defs/")])
                return resolve({**target, **{k: v for k, v in node.items() if k != "$ref"}})
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    result: dict[str, Any] = resolve(schema)
    return result


class PluginRegistry:
    def __init__(
        self,
        plugins: Sequence[LoadedPlugin],
        config: Mapping[str, str] | None = None,
        *,
        plugin_settings: Mapping[str, Mapping[str, str]] | None = None,
        log_dir: Path | None = None,
    ):
        self.plugins = list(plugins)
        self.config: Mapping[str, str] = dict(config or {})
        self.plugin_settings = {k: dict(v) for k, v in (plugin_settings or {}).items()}
        self.log_dir = log_dir

    # --- discovery ---

    @classmethod
    def discover(
        cls,
        plugin_dirs: Iterable[Path],
        *,
        config: Mapping[str, str] | None = None,
        disabled: Iterable[str] = (),
        cache_path: Path | None = None,
        log_dir: Path | None = None,
        use_entry_points: bool = True,
        plugin_settings: Mapping[str, Mapping[str, str]] | None = None,
    ) -> PluginRegistry:
        """Load every plugin found; a broken plugin is reported, never fatal."""
        disabled_ids = set(disabled)
        cache = _read_cache(cache_path)
        found: list[tuple[Path, Path]] = []  # (manifest path, plugin root)
        for folder in plugin_dirs:
            for manifest_path in sorted(Path(folder).glob(f"*/{MANIFEST_NAME}")):
                found.append((manifest_path, manifest_path.parent))
        if use_entry_points:
            found.extend(_entry_point_manifests())

        plugins: list[LoadedPlugin] = []
        seen_ids: set[str] = set()
        seen_components: dict[str, str] = {}
        for manifest_path, root in found:
            try:
                manifest = load_manifest(manifest_path)
            except Exception as exc:
                bad = PluginManifest.model_construct(
                    id=root.name,
                    name=root.name,
                    version="?",
                    api_version="?",
                    roles=[],
                    entry="?:?",
                )
                plugins.append(LoadedPlugin(bad, root, "error", f"invalid manifest: {exc}"))
                continue
            if manifest.id in seen_ids:
                continue  # a folder plugin shadows an installed one with the same id
            seen_ids.add(manifest.id)
            plugin = LoadedPlugin(manifest, root)
            plugins.append(plugin)
            if manifest.id in disabled_ids:
                plugin.status = "disabled"
                continue
            log_path = log_dir / f"{manifest.id}.log" if log_dir else None
            _load(plugin, cache, log_path, (plugin_settings or {}).get(manifest.id, {}))
            for info in plugin.components:
                owner = seen_components.get(info.id)
                if owner is not None:
                    plugin.status = "error"
                    plugin.error = f"component id {info.id!r} is already provided by {owner!r}"
                    break
            else:
                seen_components.update({info.id: manifest.id for info in plugin.components})
        if cache_path is not None:
            _write_cache(cache_path, plugins)
        return cls(plugins, config, plugin_settings=plugin_settings, log_dir=log_dir)

    # --- lookup ---

    def plugin(self, plugin_id: str) -> LoadedPlugin:
        for plugin in self.plugins:
            if plugin.manifest.id == plugin_id:
                return plugin
        raise ComponentNotFound(f"no plugin {plugin_id!r}")

    def components(self, role: RoleName | None = None) -> list[ComponentInfo]:
        return [
            info
            for plugin in self.plugins
            if plugin.usable
            for info in plugin.components
            if role is None or info.role == role
        ]

    def resolve(self, role: RoleName, ref: str) -> ComponentHandle:
        """Find a component by id (``namespace.name``) or by its plugin id."""
        for plugin in self.plugins:
            for info in plugin.components:
                if info.role == role and info.id == ref:
                    return self._handle(plugin, info)
        for plugin in self.plugins:
            if plugin.manifest.id == ref:
                candidates = [i for i in plugin.components if i.role == role]
                if len(candidates) == 1:
                    return self._handle(plugin, candidates[0])
                if not plugin.usable:
                    raise ComponentNotFound(self._unusable(plugin))
                raise ComponentNotFound(
                    f"plugin {ref!r} has {len(candidates)} {role} components; name one of: "
                    + ", ".join(i.id for i in candidates)
                )
        available = ", ".join(i.id for i in self.components(role)) or "none"
        raise ComponentNotFound(f"no {role} component {ref!r}; available: {available}")

    def _handle(self, plugin: LoadedPlugin, info: ComponentInfo) -> ComponentHandle:
        if not plugin.usable:
            raise ComponentNotFound(self._unusable(plugin))
        settings = self.plugin_settings.get(plugin.manifest.id, {})
        config = {**self.config, **{f"setting.{k}": v for k, v in settings.items()}}
        return ComponentHandle(info, plugin, config)

    # --- management ---

    def set_enabled(self, plugin_id: str, enabled: bool) -> LoadedPlugin:
        """Disable (stops its process) or re-enable a plugin without restarting the host."""
        plugin = self.plugin(plugin_id)
        if not enabled:
            if plugin.runner is not None:
                plugin.runner.close()
            plugin.runner, plugin.status, plugin.error = None, "disabled", None
        elif plugin.status == "disabled":
            plugin.status, plugin.error = "ok", None
            log_path = self.log_dir / f"{plugin_id}.log" if self.log_dir else None
            _load(plugin, {}, log_path, self.plugin_settings.get(plugin_id, {}))
        return plugin

    def check_health(self, plugin_id: str) -> LoadedPlugin:
        """Ask the plugin now (a subprocess plugin is started for this)."""
        plugin = self.plugin(plugin_id)
        if plugin.runner is None or plugin.status == "disabled":
            return plugin
        try:
            plugin.health = plugin.runner.health()
        except PluginError as exc:
            plugin.status, plugin.error = "error", exc.message
            return plugin
        if plugin.health.status == "error":
            plugin.status, plugin.error = "error", f"health check: {plugin.health.message}"
        else:
            plugin.status = "degraded" if plugin.health.status == "degraded" else "ok"
            plugin.error = None
        return plugin

    @staticmethod
    def _unusable(plugin: LoadedPlugin) -> str:
        return f"plugin {plugin.manifest.id!r} is {plugin.status}: {plugin.error or 'disabled'}"

    def union_schema(self, role: RoleName, discriminator: str) -> dict[str, Any]:
        """JSON Schema accepting ``{discriminator: <component id>, params: {...}}`` (ADR-003)."""
        variants = [
            {
                "type": "object",
                "title": info.id,
                "description": info.description,
                "properties": {
                    discriminator: {"const": info.id},
                    "params": _inline_refs(info.params_schema),
                },
                "required": [discriminator],
            }
            for info in self.components(role)
        ]
        return {"oneOf": variants, "discriminator": {"propertyName": discriminator}}

    # --- lifecycle ---

    def close(self) -> None:
        for plugin in self.plugins:
            if plugin.runner is not None:
                plugin.runner.close()

    def __enter__(self) -> PluginRegistry:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def _entry_point_manifests() -> list[tuple[Path, Path]]:
    found = []
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        package = ep.value.split(":")[0].split(".")[0]
        try:
            manifest = Path(str(files(package) / MANIFEST_NAME))
        except ModuleNotFoundError:
            continue
        if manifest.is_file():
            found.append((manifest, manifest.parent))
    return found


def _load(
    plugin: LoadedPlugin,
    cache: dict[str, Any],
    log_path: Path | None,
    settings: Mapping[str, str],
) -> None:
    manifest = plugin.manifest
    if manifest.api_version != API_VERSION:
        plugin.status = "error"
        plugin.error = f"api_version {manifest.api_version} is not supported (host: {API_VERSION})"
        return
    try:
        if manifest.runtime == "inprocess":
            plugin.runner = InProcessRunner(_import_plugin(manifest, plugin.root))
            described = plugin.runner.describe()
        else:
            runner = SubprocessRunner(manifest, plugin.root, log_path=log_path, settings=settings)
            plugin.runner = runner
            described = cache.get(_cache_key(plugin)) or runner.describe()
        plugin.components = _component_infos(manifest, described)
        # A subprocess plugin listed from the cache is not started just for a health check.
        if isinstance(plugin.runner, InProcessRunner) or _started(plugin.runner):
            plugin.health = plugin.runner.health()
            if plugin.health.status == "error":
                plugin.status, plugin.error = "error", f"health check: {plugin.health.message}"
            elif plugin.health.status == "degraded":
                plugin.status = "degraded"
    except Exception as exc:
        plugin.status = "error"
        plugin.error = (
            exc.message if isinstance(exc, PluginError) else f"{type(exc).__name__}: {exc}"
        )
        if plugin.runner is not None:
            plugin.runner.close()


def _started(runner: Runner) -> bool:
    return isinstance(runner, SubprocessRunner) and runner.started


def _import_plugin(manifest: PluginManifest, root: Path) -> Plugin:
    src = root / "src"
    path = str(src if src.is_dir() else root)
    existing = sys.modules.get(manifest.entry_module)
    existing_file = getattr(existing, "__file__", None)
    if existing_file and not Path(existing_file).resolve().is_relative_to(root.resolve()):
        raise PluginHostError(
            f"module {manifest.entry_module!r} is already imported from {existing_file}"
        )
    if path not in sys.path:
        sys.path.insert(0, path)
    module = importlib.import_module(manifest.entry_module)
    plugin = getattr(module, manifest.entry.split(":")[1])
    if not isinstance(plugin, Plugin):
        raise PluginHostError(f"{manifest.entry} is not an atelier_plugin_sdk.Plugin")
    return plugin


def _component_infos(
    manifest: PluginManifest, described: list[dict[str, Any]]
) -> list[ComponentInfo]:
    infos = []
    for item in described:
        role = item["role"]
        if role not in ROLE_NAMES or role not in manifest.roles:
            raise PluginHostError(
                f"component {item['name']!r} has role {role!r} not in the manifest"
            )
        infos.append(
            ComponentInfo(
                id=f"{manifest.effective_namespace}.{item['name']}",
                plugin_id=manifest.id,
                role=role,
                name=item["name"],
                version=item["version"],
                description=item.get("description", ""),
                params_schema=item["params_schema"],
            )
        )
    return infos


def _cache_key(plugin: LoadedPlugin) -> str:
    """Version plus the newest change to the plugin's own files: an edited plugin is described
    again without a version bump. What it reads elsewhere (Pygmalion's backends) is picked up
    after ``atelier update`` drops the cache."""
    newest = max(
        (p.stat().st_mtime_ns for p in plugin.root.rglob("*") if p.suffix in (".py", ".toml")),
        default=0,
    )
    return f"{plugin.manifest.id}@{plugin.manifest.version}:{newest}"


def _read_cache(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data
    except (OSError, json.JSONDecodeError):
        return {}


def _write_cache(path: Path, plugins: Sequence[LoadedPlugin]) -> None:
    cache = _read_cache(path)
    for plugin in plugins:
        if plugin.manifest.runtime == "subprocess" and plugin.components:
            cache[_cache_key(plugin)] = [
                {
                    "role": i.role,
                    "name": i.name,
                    "version": i.version,
                    "description": i.description,
                    "params_schema": i.params_schema,
                }
                for i in plugin.components
            ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")

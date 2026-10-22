"""Entry point of the ``atelier`` command line: every platform action without the UI."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from atelier_cli import __version__
from atelier_domain import AnimTrack, Vec3, ViewerDirection
from atelier_plugin_host import ComponentHandle, LoadedPlugin, SubprocessRunner
from atelier_plugin_sdk import ROLE_NAMES, PluginError
from atelier_plugin_sdk.roles import RoleName
from atelier_plugin_sdk.template import TEMPLATE_ROLES, create_plugin
from atelier_plugin_sdk.testing import run_contract_suite
from atelier_show_engine.compiler import CompileError
from atelier_show_engine.config import find_config, load_config
from atelier_show_engine.importing import AssetImportError, UpAxis
from atelier_show_engine.service import ServiceError, ShowService
from atelier_show_engine.store import ShowStore, StoreError

app = typer.Typer(
    name="atelier", help="Atelier: from a drone-show idea to fleet files.", no_args_is_help=True
)
fleet_app = typer.Typer(help="Fleet limits and presets.", no_args_is_help=True)
music_app = typer.Typer(help="The show's music.", no_args_is_help=True)
scene_app = typer.Typer(help="Scenes of the program.", no_args_is_help=True)
pipeline_app = typer.Typer(help="Which component runs each stage.", no_args_is_help=True)
plugin_app = typer.Typer(help="Plugins: list, create, test.", no_args_is_help=True)
generate_app = typer.Typer(help="Generation: concept images and 3D models.", no_args_is_help=True)
app.add_typer(fleet_app, name="fleet")
app.add_typer(music_app, name="music")
app.add_typer(scene_app, name="scene")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(plugin_app, name="plugin")
app.add_typer(generate_app, name="generate")

console = Console(highlight=False)
SHOW_ENV = "ATELIER_SHOW"
ShowOption = Annotated[
    str | None, typer.Option("--show", "-s", help="Show id or folder (default: the current show)")
]
EXPECTED_ERRORS = (
    ServiceError,
    CompileError,
    StoreError,
    AssetImportError,
    PluginError,
    KeyError,
    ValueError,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"atelier {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_print_version, is_eager=True, help="Show version and exit."
        ),
    ] = False,
) -> None:
    """Atelier: from a drone-show idea to fleet files."""


# --- helpers -------------------------------------------------------------------------------


@contextmanager
def service() -> Iterator[ShowService]:
    svc = ShowService(load_config())
    try:
        yield svc
    except EXPECTED_ERRORS as exc:
        message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
        console.print(f"[red]error:[/red] {message}")
        raise typer.Exit(1) from None
    finally:
        svc.close()


def _current_file(svc: ShowService) -> Path:
    return svc.config.cache_dir / "current_show"


def remember(svc: ShowService, store: ShowStore) -> None:
    path = _current_file(svc)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(store.root), encoding="utf-8")


def open_show(svc: ShowService, ref: str | None) -> ShowStore:
    ref = ref or os.environ.get(SHOW_ENV)
    if ref is None and (Path.cwd() / "show.json").is_file():
        ref = str(Path.cwd())
    if ref is None and _current_file(svc).is_file():
        ref = _current_file(svc).read_text(encoding="utf-8").strip()
    if ref is None:
        raise ServiceError("no show selected: pass --show, run `atelier use <id>` or `atelier new`")
    store = svc.open(ref)
    remember(svc, store)
    return store


def parse_value(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def parse_params(pairs: list[str]) -> dict[str, Any]:
    params = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ServiceError(f"expected key=value, got {pair!r}")
        params[key.strip()] = parse_value(value)
    return params


def parse_vec(text: str) -> Vec3:
    parts = [float(v) for v in text.split(",")]
    if len(parts) != 3:
        raise ServiceError(f"expected x,y,z, got {text!r}")
    return parts[0], parts[1], parts[2]


def parse_track(spec: str) -> AnimTrack:
    """``"builtin.rotate angle_deg=90 easing=ease_in_out start=2 duration=10 loop=false"``."""
    primitive, *rest = shlex.split(spec)
    values = parse_params(rest)
    data: dict[str, Any] = {"primitive": primitive}
    for key, field in (
        ("start", "start_s"),
        ("duration", "duration_s"),
        ("easing", "easing"),
        ("loop", "loop"),
        ("region", "region"),
    ):
        if key in values:
            data[field] = values.pop(key)
    data["params"] = values
    return AnimTrack.model_validate(data)


def progress_printer() -> Callable[[float, str], None]:
    last = [""]

    def report(fraction: float, message: str) -> None:
        if message != last[0]:
            console.print(f"  [dim]{fraction:4.0%}[/dim] {message}")
            last[0] = message

    return report


# --- shows ---------------------------------------------------------------------------------


@app.command()
def new(
    title: str,
    drones: Annotated[int, typer.Option(help="Fleet size")],
    duration: Annotated[float, typer.Option(help="Time budget of the show, seconds")] = 600.0,
    preset: Annotated[str, typer.Option(help="Fleet preset")] = "conservative",
    viewer: Annotated[str, typer.Option(help="Audience side: -Y, +Y, -X, +X")] = "-Y",
    seed: int = 0,
) -> None:
    """Create a show and make it the current one."""
    with service() as svc:
        store, show = svc.create_show(
            title,
            drones=drones,
            duration_s=duration,
            preset=preset,
            viewer=cast(ViewerDirection, viewer),
            seed=seed,
        )
        remember(svc, store)
        console.print(f"created [bold]{show.id}[/bold] in {store.root}")


@app.command("list")
def list_shows() -> None:
    """List shows in the projects folder."""
    with service() as svc:
        table = Table("id", "title", "drones", "scenes", "revision")
        for show in svc.list_shows():
            table.add_row(
                show.id,
                show.brief.title,
                str(show.brief.fleet.drone_count),
                str(len(show.program)),
                str(show.revision),
            )
        console.print(table)


@app.command()
def use(show: str) -> None:
    """Make a show the current one."""
    with service() as svc:
        store = open_show(svc, show)
        console.print(f"current show: {store.root.name}")


# --- fleet and music -----------------------------------------------------------------------


@fleet_app.command("presets")
def fleet_presets() -> None:
    """Built-in and configured fleet presets."""
    with service() as svc:
        table = Table("preset", "min dist", "h speed", "up", "down", "accel", "alt", "grid")
        for name, p in svc.config.presets.items():
            table.add_row(
                name,
                f"{p.min_distance_m:g}",
                f"{p.max_speed_h_ms:g}",
                f"{p.max_speed_up_ms:g}",
                f"{p.max_speed_down_ms:g}",
                f"{p.max_accel_ms2:g}",
                f"{p.altitude_min_m:g}-{p.altitude_max_m:g}",
                f"{p.takeoff_grid_spacing_m:g}",
            )
        console.print(table)


@fleet_app.command("use")
def fleet_use(preset: str, show: ShowOption = None) -> None:
    """Copy a preset's limits into the show."""
    with service() as svc:
        svc.use_fleet_preset(open_show(svc, show), preset)
        console.print(f"fleet preset: {preset}")


@fleet_app.command("set")
def fleet_set(
    show: ShowOption = None,
    drones: Annotated[int | None, typer.Option(help="Fleet size")] = None,
    min_distance: float | None = None,
    max_speed_h: float | None = None,
    max_speed_up: float | None = None,
    max_speed_down: float | None = None,
    max_accel: float | None = None,
    altitude_min: float | None = None,
    altitude_max: float | None = None,
    grid_spacing: float | None = None,
) -> None:
    """Change individual fleet limits."""
    with service() as svc:
        result = svc.set_fleet(
            open_show(svc, show),
            drone_count=drones,
            min_distance_m=min_distance,
            max_speed_h_ms=max_speed_h,
            max_speed_up_ms=max_speed_up,
            max_speed_down_ms=max_speed_down,
            max_accel_ms2=max_accel,
            altitude_min_m=altitude_min,
            altitude_max_m=altitude_max,
            takeoff_grid_spacing_m=grid_spacing,
        )
        console.print(result.brief.fleet.model_dump_json(indent=2))


@music_app.command("set")
def music_set(
    file: Path,
    offset: Annotated[
        float | None, typer.Option(help="Show time where the music starts, s")
    ] = None,
    show: ShowOption = None,
) -> None:
    """Attach a music file (WAV, FLAC, OGG, MP3; Skybrush embeds MP3 only)."""
    with service() as svc:
        result = svc.set_music(open_show(svc, show), file, offset)
        assert result.audio is not None
        console.print(f"music {file.name}: {result.audio.duration_s:.1f} s")


# --- models, points, scenes ----------------------------------------------------------------


@app.command("add-model")
def add_model(
    glb: Annotated[
        Path | None, typer.Option("--glb", help="GLB/GLTF/OBJ/STL/PLY file (none: empty card)")
    ] = None,
    name: str | None = None,
    height: Annotated[
        float | None, typer.Option(help="Figure height, m (default: from spacing)")
    ] = None,
    up: Annotated[str, typer.Option(help="Up axis of the file: y (glTF) or z")] = "y",
    color_source: Annotated[str, typer.Option(help="texture, palette or solid")] = "texture",
    palette: Annotated[list[str] | None, typer.Option(help="Hex colour, repeatable")] = None,
    show: ShowOption = None,
) -> None:
    """Import a 3D model as a model card, or create an empty card for generation."""
    with service() as svc:
        if glb is None:
            if not name:
                raise ServiceError("an empty model card needs --name")
            _, model_id = svc.create_model(
                open_show(svc, show),
                name,
                target_height_m=height,
                color_source=color_source,
                palette=palette or [],
            )
            console.print(f"model [bold]{model_id}[/bold] (empty card: generate images, then mesh)")
            return
        _, model_id = svc.add_model(
            open_show(svc, show),
            glb,
            name=name,
            target_height_m=height,
            up=cast(UpAxis, up),
            color_source=color_source,
            palette=palette or [],
        )
        console.print(f"model [bold]{model_id}[/bold]")


@app.command()
def points(
    model: str,
    n: Annotated[int | None, typer.Option(help="Drone count (default: the model's)")] = None,
    show: ShowOption = None,
) -> None:
    """Distribute drones over a model with the pipeline's point_distributor."""
    with service() as svc:
        record = svc.compute_points(open_show(svc, show), model, n, on_progress=progress_printer())
        console.print(f"{record.slot} by {record.producer.component} -> {record.path}")


@generate_app.command("images")
def generate_images(
    model: str,
    prompt: Annotated[str, typer.Option(help="What to draw")],
    n: Annotated[int, typer.Option("--n", "-n", help="How many concepts")] = 4,
    show: ShowOption = None,
) -> None:
    """Concept images for a model with the pipeline's image_generator (GPU)."""
    with service() as svc:
        store = open_show(svc, show)
        record = svc.generate_concepts(store, model, prompt, n, on_progress=progress_printer())
        console.print(f"{record.slot} by {record.producer.component} -> {record.path}")
        for concept in svc.concept_images(store, model):
            console.print(f"  {concept}")


@generate_app.command("mesh")
def generate_mesh(
    model: str,
    concept: Annotated[
        str | None, typer.Option(help="<artifact>/<file> (default: selected or newest concept)")
    ] = None,
    show: ShowOption = None,
) -> None:
    """A 3D model from a concept image with the pipeline's mesh_generator (GPU)."""
    with service() as svc:
        record = svc.generate_mesh(
            open_show(svc, show), model, concept, on_progress=progress_printer()
        )
        console.print(f"{record.slot} by {record.producer.component} -> {record.path}")


@app.command()
def ui(
    port: Annotated[int, typer.Option(help="Port of the web UI and API")] = 8710,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Do not open a browser")] = False,
    no_worker: Annotated[
        bool, typer.Option("--no-worker", help="Do not start a worker (one runs elsewhere)")
    ] = False,
) -> None:
    """Start the web UI: the API on 127.0.0.1 and a worker process for the jobs."""
    import threading
    import webbrowser

    import uvicorn

    from atelier_api.app import create_app
    from atelier_plugin_host.runners import kill_process_tree

    config = load_config()
    dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if not (dist / "index.html").is_file():
        console.print(
            "[yellow]the web UI is not built:[/yellow] npm --prefix apps/web install && "
            "npm --prefix apps/web run build  (the API works without it)"
        )
    worker_proc = None
    if not no_worker:
        log_path = config.cache_dir / "logs" / "worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        with log_path.open("a", encoding="utf-8") as log_file:
            worker_proc = subprocess.Popen(
                [sys.executable, "-m", "atelier_worker.worker"],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=config.root,
                creationflags=flags,
            )
        console.print(f"worker started (pid {worker_proc.pid}), log: {log_path}")
    url = f"http://127.0.0.1:{port}/"
    if not no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    console.print(f"Atelier UI: {url}  (Ctrl+C to stop)")
    try:
        uvicorn.run(
            create_app(config, web_dist=dist), host="127.0.0.1", port=port, log_level="warning"
        )
    finally:
        if worker_proc is not None:
            kill_process_tree(worker_proc)  # type: ignore[arg-type]


@app.command()
def worker() -> None:
    """Run the job worker in this terminal (the UI starts one by itself)."""
    from atelier_worker.worker import main as worker_main

    raise typer.Exit(worker_main())


@scene_app.command("add")
def scene_add(
    model: Annotated[str, typer.Option(help="Model id")],
    hold: Annotated[float, typer.Option(help="Hold duration, s")],
    title: str | None = None,
    transition: Annotated[
        float | None, typer.Option(help="Transition in, s (default: minimal safe)")
    ] = None,
    track: Annotated[
        list[str] | None,
        typer.Option(help='Animation track, e.g. "builtin.rotate angle_deg=90 easing=ease_in_out"'),
    ] = None,
    offset: Annotated[str, typer.Option(help="x,y,z shift, m")] = "0,0,0",
    rotation: Annotated[str, typer.Option(help="x,y,z Euler degrees")] = "0,0,0",
    planner: Annotated[str | None, typer.Option(help="transition_planner component")] = None,
    show: ShowOption = None,
) -> None:
    """Append a scene to the program."""
    with service() as svc:
        scene = svc.add_scene(
            open_show(svc, show),
            model,
            hold_s=hold,
            title=title,
            transition_s=transition,
            planner=planner,
            tracks=[parse_track(t) for t in track or []],
            offset_m=parse_vec(offset),
            rotation_deg=parse_vec(rotation),
        )
        console.print(f"scene [bold]{scene.id}[/bold]: {scene.title}")


@scene_app.command("list")
def scene_list(show: ShowOption = None) -> None:
    """The program, in order."""
    with service() as svc:
        current = open_show(svc, show).load()
        table = Table("id", "title", "model", "transition", "hold", "tracks")
        for s in current.program:
            tracks = ", ".join(t.primitive for t in s.animation.tracks) if s.animation else ""
            transition = (
                f"{s.transition_in.duration_s:g} s" if s.transition_in.duration_s else "auto"
            )
            table.add_row(s.id, s.title, s.model_id, transition, f"{s.hold_s:g} s", tracks)
        console.print(table)


# --- pipeline and status -------------------------------------------------------------------


@pipeline_app.command("show")
def pipeline_show(show: ShowOption = None) -> None:
    """The show's pipeline."""
    with service() as svc:
        console.print(open_show(svc, show).load().pipeline.model_dump_json(indent=2))


@pipeline_app.command("set")
def pipeline_set(
    role: Annotated[str, typer.Argument(help="point_distributor or transition_planner")],
    component: str,
    param: Annotated[list[str] | None, typer.Option(help="key=value, repeatable")] = None,
    show: ShowOption = None,
) -> None:
    """Choose the component (and its params) for a stage."""
    with service() as svc:
        svc.set_stage(
            open_show(svc, show), cast(RoleName, role), component, parse_params(param or [])
        )
        console.print(f"{role}: {component}")


@pipeline_app.command("validators")
def pipeline_validators(components: str, show: ShowOption = None) -> None:
    """Comma-separated validator_rule components."""
    with service() as svc:
        svc.set_stage_list(open_show(svc, show), "validators", components.split(","))


@pipeline_app.command("exporters")
def pipeline_exporters(components: str, show: ShowOption = None) -> None:
    """Comma-separated exporter components."""
    with service() as svc:
        svc.set_stage_list(open_show(svc, show), "exporters", components.split(","))


@app.command()
def status(show: ShowOption = None) -> None:
    """Models, program and which artifacts are current or stale."""
    with service() as svc:
        store = open_show(svc, show)
        current = store.load()
        fleet = current.brief.fleet
        console.print(
            f"[bold]{current.brief.title}[/bold] ({current.id}, revision {current.revision}): "
            f"{fleet.drone_count} drones, preset {fleet.preset or 'custom'}, {current.fps} fps, "
            f"{len(current.models)} models, {len(current.program)} scenes"
        )
        if current.audio:
            console.print(
                f"music: {current.audio.asset.original_name} ({current.audio.duration_s:.1f} s)"
            )
        table = Table("artifact", "state")
        for slot, reason in svc.stale_report(store).items():
            table.add_row(
                slot,
                "[green]current[/green]" if reason is None else f"[yellow]stale[/yellow]: {reason}",
            )
        console.print(table)


# --- compile, validate, export -------------------------------------------------------------


@app.command("compile")
def compile_show(
    allow_stale: Annotated[bool, typer.Option(help="Use stale inputs anyway")] = False,
    show: ShowOption = None,
) -> None:
    """Compile the program into trajectories."""
    with service() as svc:
        record, report = svc.compile(
            open_show(svc, show), allow_stale=allow_stale, on_progress=progress_printer()
        )
        console.print(
            f"compiled {report.drones} drones, {report.frames} frames, {report.duration_s:.1f} s "
            f"(budget {report.budget_s:g} s) -> {record.path}"
        )
        if report.music_start_s is not None:
            console.print(f"music starts at {report.music_start_s:.1f} s")
        for warning in report.warnings:
            console.print(f"[yellow]warning:[/yellow] {warning}")


@app.command()
def validate(
    allow_stale: Annotated[bool, typer.Option(help="Validate a stale compile")] = False,
    show: ShowOption = None,
) -> None:
    """Run the pipeline's validators over the compiled show."""
    with service() as svc:
        summary = svc.validate(open_show(svc, show), allow_stale=allow_stale)
        for key, value in summary.metrics.items():
            console.print(f"  {key}: {value:.3f}")
        for v in summary.violations[:50]:
            where = f"{v.scene_id or v.segment or ''}".strip()
            console.print(f"[red]{v.rule}[/red] {where}: {v.message}")
        if len(summary.violations) > 50:
            console.print(f"... {len(summary.violations) - 50} more in {summary.path}")
        verdict = "[green]OK[/green]" if summary.ok else f"[red]{summary.errors} violations[/red]"
        console.print(f"{verdict} -> {summary.path}")
        if not summary.ok:
            raise typer.Exit(2)


@app.command()
def export(
    exporter: Annotated[
        list[str] | None, typer.Option(help="Only these exporters, repeatable")
    ] = None,
    allow_stale: Annotated[bool, typer.Option(help="Export a stale compile")] = False,
    show: ShowOption = None,
) -> None:
    """Run the exporters (Blender builds a Skybrush .blend; CSV for Skybrush import)."""
    with service() as svc:
        for result in svc.export(
            open_show(svc, show),
            exporter or None,
            allow_stale=allow_stale,
            on_progress=progress_printer(),
        ):
            console.print(f"[bold]{result.component}[/bold] -> {result.folder}")
            for file in result.files:
                console.print(f"  {file.name}")


@app.command()
def history(show: ShowOption = None) -> None:
    """Revisions of the show."""
    with service() as svc:
        table = Table("rev", "when", "who", "change")
        for r in open_show(svc, show).history():
            table.add_row(str(r.revision), r.created_at, r.author, r.message)
        console.print(table)


@app.command()
def rollback(revision: int, show: ShowOption = None) -> None:
    """Make an earlier revision current again (as a new revision)."""
    with service() as svc:
        result = open_show(svc, show).rollback(revision)
        console.print(f"revision {result.revision} = content of revision {revision}")


# --- doctor --------------------------------------------------------------------------------


@app.command()
def doctor() -> None:
    """Check the configuration, Blender, the Skybrush add-on and the plugins."""
    with service() as svc:
        config = svc.config
        found = find_config()
        console.print(
            f"config: {found or 'none (defaults; copy atelier.example.toml to atelier.toml)'}"
        )
        console.print(f"projects: {config.projects_dir}")
        blender = config.blender_exe
        if blender is None or not blender.is_file():
            console.print(f"[red]blender:[/red] not found ({blender}); set paths.blender_exe")
        else:
            probe = (
                f"import addon_utils, sys; m = sys.modules.get('{config.blender.skybrush_addon}');"
                f"print('ADDON', addon_utils.check('{config.blender.skybrush_addon}')[1],"
                " getattr(m, 'bl_info', {}).get('version'))"
            )
            try:
                run = subprocess.run(
                    [str(blender), "-b", "--offline-mode", "--python-expr", probe],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                lines = run.stdout.splitlines()
                version = next((line for line in lines if line.startswith("Blender ")), "?")
                addon = next((line for line in lines if line.startswith("ADDON")), "ADDON ?")
                console.print(f"blender: {version} ({blender})")
                state = addon.removeprefix("ADDON ")
                console.print(f"skybrush add-on {config.blender.skybrush_addon}: {state}")
            except subprocess.TimeoutExpired:
                console.print("[red]blender:[/red] did not answer within 120 s")
        _plugin_table(svc.registry.plugins)


# --- plugins -------------------------------------------------------------------------------


def _plugin_table(plugins: list[LoadedPlugin]) -> None:
    table = Table("plugin", "version", "runtime", "status", "components")
    for plugin in plugins:
        manifest = plugin.manifest
        state = {"ok": "[green]ok[/green]", "degraded": "[yellow]degraded[/yellow]"}.get(
            plugin.status, f"[red]{plugin.status}[/red]: {plugin.error}"
        )
        components = "\n".join(f"{c.id} ({c.role})" for c in plugin.components)
        table.add_row(manifest.id, manifest.version, manifest.runtime, state, components)
    console.print(table)


@plugin_app.command("list")
def plugin_list() -> None:
    """Installed plugins, their components and health."""
    with service() as svc:
        _plugin_table(svc.registry.plugins)


@plugin_app.command("new")
def plugin_new(
    name: Annotated[str, typer.Argument(help="Plugin id, lower_snake_case")],
    role: Annotated[
        str, typer.Option(help=f"One of: {', '.join(TEMPLATE_ROLES)}")
    ] = "point_distributor",
    namespace: str | None = None,
) -> None:
    """Create a working plugin skeleton in the first plugins folder."""
    with service() as svc:
        if role not in ROLE_NAMES:
            raise ServiceError(f"unknown role {role!r}")
        root = create_plugin(svc.config.plugin_dirs[0], name, role, namespace)
        console.print(f"created {root}")
        console.print(f"try: atelier plugin test {name}")


def _subprocess_handle(handle: ComponentHandle) -> ComponentHandle:
    """The same component, run through the SDK's subprocess server."""
    plugin = handle.plugin
    clone = LoadedPlugin(
        plugin.manifest,
        plugin.root,
        plugin.status,
        plugin.error,
        plugin.health,
        plugin.components,
        SubprocessRunner(plugin.manifest, plugin.root),
    )
    return ComponentHandle(handle.info, clone, handle.config)


@plugin_app.command("test")
def plugin_test(
    plugin_id: str,
    runtime: Annotated[str, typer.Option(help="both, inprocess or subprocess")] = "both",
) -> None:
    """Run the contract suite of every component of a plugin."""
    with service() as svc:
        plugin = svc.registry.plugin(plugin_id)
        if not plugin.usable:
            raise ServiceError(f"plugin {plugin_id} is {plugin.status}: {plugin.error}")
        failed = 0
        workdir = svc.config.cache_dir / "plugin-tests" / plugin_id
        for info in plugin.components:
            role: RoleName = info.role
            handle = svc.registry.resolve(role, info.id)
            modes = {"inprocess": handle}
            if plugin.manifest.runtime == "inprocess" and runtime in ("both", "subprocess"):
                modes["subprocess"] = _subprocess_handle(handle)
            if runtime == "inprocess":
                modes.pop("subprocess", None)
            if runtime == "subprocess" and plugin.manifest.runtime == "inprocess":
                modes.pop("inprocess")
            for mode, h in modes.items():
                try:
                    results = run_contract_suite(h, workdir / info.name / mode)
                finally:
                    if mode == "subprocess":
                        h.runner.close()
                for r in results:
                    mark = (
                        "[green]ok[/green]"
                        if r.ok
                        else ("[red]FAIL[/red]" if r.level == "error" else "[yellow]warn[/yellow]")
                    )
                    console.print(f"{mark} {info.id} {escape(f'[{mode}]')} {r.name} {r.detail}")
                    failed += not r.ok and r.level == "error"
        if failed:
            raise typer.Exit(1)

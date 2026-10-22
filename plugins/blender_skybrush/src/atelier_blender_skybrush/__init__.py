"""Exporter: a Skybrush Studio ``.blend`` built in headless Blender (ADR-010).

Trajectories come from Atelier; Blender only receives keyframes (reduced within a tolerance),
the storyboard (takeoff, one entry per scene hold, landing), the fleet's safety thresholds and
the music. No Skybrush server is used. Skybrush embeds music into ``.skyc`` only from MP3.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from atelier_blender_bridge import SCRIPTS_DIR, BlenderError, build_keys, run_blender, save_keys
from atelier_plugin_sdk import (
    CompiledShow,
    CompiledShowRef,
    Exporter,
    ExportResult,
    Health,
    Plugin,
    PluginError,
    RunContext,
)


class BlendParams(BaseModel):
    key_tolerance_m: float = Field(0.02, gt=0, description="Max position error of the keyframes")
    color_tolerance: float = Field(2 / 255, gt=0, description="Max LED colour error (0..1)")
    verify_frames: int = Field(12, ge=2, description="Frames checked against the trajectories")
    max_error_m: float = Field(0.05, gt=0, description="Fail when the .blend is off by more")


def storyboard(
    compiled: CompiledShow,
) -> tuple[list[dict[str, Any]], dict[str, np.ndarray[Any, Any]]]:
    """Skybrush storyboard entries and their formation positions, from the timeline marks."""
    entries: list[dict[str, Any]] = []
    formations: dict[str, np.ndarray[Any, Any]] = {}
    last = compiled.frame_count - 1
    entries.append({"name": "Takeoff grid", "purpose": "TAKEOFF", "frame_start": 0, "duration": 1})
    formations["Takeoff grid"] = compiled.positions[0]
    for mark in compiled.marks:
        if mark.kind == "hold":
            name = f"{mark.title} ({mark.scene_id})"
            entries.append(
                {
                    "name": name,
                    "purpose": "SHOW",
                    "frame_start": mark.start_frame,
                    "duration": mark.end_frame - mark.start_frame + 1,
                }
            )
            formations[name] = compiled.positions[mark.start_frame]
    entries.append({"name": "Landing", "purpose": "LANDING", "frame_start": last, "duration": 1})
    formations["Landing"] = compiled.positions[last]
    for index, entry in enumerate(entries):
        entry["positions_key"] = f"formation_{index}"
    return entries, {e["positions_key"]: formations[e["name"]] for e in entries}


class Blend(Exporter[BlendParams]):
    """Skybrush Studio .blend: drones, LED colours, storyboard, safety limits and music."""

    name = "blend"
    version = "0.1.0"
    Params = BlendParams

    def export(
        self, ctx: RunContext, show: CompiledShowRef, out_dir: Path, params: BlendParams
    ) -> ExportResult:
        blender = ctx.config.get("blender_exe")
        if not blender:
            raise PluginError("Blender is not configured: set paths.blender_exe in atelier.toml")
        compiled = show.load()
        fleet = compiled.meta.get("fleet")
        if not fleet:
            raise PluginError("the compiled show carries no fleet limits; recompile it")
        ctx.progress(0.05, "reducing keyframes")
        keys = build_keys(
            compiled.positions, compiled.colors, params.key_tolerance_m, params.color_tolerance
        )
        entries, formations = storyboard(compiled)
        verify = np.unique(
            np.linspace(0, compiled.frame_count - 1, params.verify_frames).round().astype(np.int64)
        )
        keys_path = save_keys(
            ctx.output_path("keys.npz"),
            keys,
            verify_frames=verify,
            verify_positions=compiled.positions[verify],
            **formations,
        )
        audio = None
        if compiled.audio is not None and compiled.audio.path.is_file():
            audio = {
                "path": str(compiled.audio.path),
                "frame_start": round(compiled.audio.offset_s * compiled.fps),
            }
        out_dir.mkdir(parents=True, exist_ok=True)
        blend = out_dir / "show.blend"
        ctx.progress(0.2, "building the .blend in Blender")
        try:
            result = run_blender(
                Path(blender),
                SCRIPTS_DIR / "build_show.py",
                {
                    "keys_npz": str(keys_path),
                    "out_blend": str(blend),
                    "fps": compiled.fps,
                    "frame_count": compiled.frame_count,
                    "drone_count": compiled.drone_count,
                    "addon": ctx.config.get("skybrush_addon", "ui_skybrush_studio"),
                    "safety": fleet,
                    "storyboard": entries,
                    "audio": audio,
                },
                workdir=ctx.workdir / "blender",
                log_path=out_dir / "blender.log",
                timeout_s=float(ctx.config.get("blender_timeout_s", "1800")),
            )
        except BlenderError as exc:
            raise PluginError(str(exc)) from exc
        if result["max_error_m"] > params.max_error_m:
            raise PluginError(
                f".blend differs from the trajectories by {result['max_error_m']:.3f} m "
                f"(limit {params.max_error_m} m); lower key_tolerance_m"
            )
        frames = compiled.frame_count * compiled.drone_count
        result["reduction"] = {
            "location_keys": len(keys["loc_frames"]),
            "frames_x_drones": frames,
            "ratio": round(len(keys["loc_frames"]) / frames, 4),
        }
        report = out_dir / "build_report.json"
        report.write_text(json.dumps(result, indent=2), encoding="utf-8")
        ctx.progress(1.0, f"{result['drones']} drones, {result['blend_bytes'] / 1e6:.1f} MB")
        return ExportResult(files=[blend, report], meta={"max_error_m": result["max_error_m"]})


plugin = Plugin(components=[Blend()], health_check=Health.ok)

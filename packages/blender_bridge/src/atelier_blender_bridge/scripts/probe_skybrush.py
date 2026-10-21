"""Headless probe of Skybrush Studio for Blender (phase 0 research, see docs/SKYBRUSH_NOTES.md).

Runs inside Blender and is never imported by the platform::

    blender -b --offline-mode --python-exit-code 1 --python probe_skybrush.py -- --out <dir>

Builds a tiny show the way phase 1 will (drones from the Skybrush template, location and
LED colour keyframes written with ``foreach_set``, storyboard TAKEOFF/SHOW/LANDING, a sound
strip), saves it, reopens it, steps frames so the Skybrush safety check runs, and writes
``<dir>/probe_report.json``. User preferences are never saved; no network calls are made.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import time
import traceback
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import addon_utils
import bpy
import numpy as np

FPS = 30
GRID = 4  # GRID x GRID drones
SPACING_M = 3.0
SECONDS = 4
INTERPOLATION_LINEAR = 1  # Keyframe.interpolation enum index: CONSTANT=0, LINEAR=1, BEZIER=2


class Probe:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def step(self, name: str, fn: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        entry: dict[str, Any] = {"step": name}
        try:
            entry["result"] = fn()
            entry["ok"] = True
        except Exception as exc:
            entry["ok"] = False
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["traceback"] = traceback.format_exc()
        entry["seconds"] = round(time.perf_counter() - started, 4)
        self.steps.append(entry)
        print(f"[probe] {name}: {'ok' if entry['ok'] else 'FAILED ' + entry['error']}")
        return entry.get("result")


def write_test_wav(path: Path, seconds: float = 2.0, rate: int = 22050) -> None:
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(int(seconds * rate))
    )
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(frames)


def environment() -> dict[str, Any]:
    return {
        "blender_version": bpy.app.version_string,
        "python": sys.version,
        "background": bpy.app.background,
        "online_access": bpy.app.online_access,
        "binary_path": bpy.app.binary_path,
    }


def enable_addon(module: str) -> dict[str, Any]:
    in_prefs_before = module in bpy.context.preferences.addons
    _, loaded_before = addon_utils.check(module)
    if not loaded_before:
        # default_set=False: enable for this session only, never touch user preferences.
        addon_utils.enable(module, default_set=False, handle_error=None)
    mod = sys.modules[module]
    import sbstudio

    return {
        "module": module,
        "enabled_in_prefs": in_prefs_before,
        "loaded_before_probe": loaded_before,
        "bl_info_version": list(mod.bl_info["version"]),
        "bl_info_blender": list(mod.bl_info["blender"]),
        "addon_file": mod.__file__,
        "sbstudio_path": str(Path(sbstudio.__file__).parent),
        "scene_has_skybrush": hasattr(bpy.types.Scene, "skybrush"),
    }


def setup_scene(frame_end: int) -> dict[str, Any]:
    scene = bpy.context.scene
    scene.render.fps = FPS
    scene.render.fps_base = 1.0
    scene.frame_start = 1
    scene.frame_end = frame_end
    scene.skybrush.version = 2  # skip legacy migrations on load
    result = bpy.ops.skybrush.prepare()
    return {
        "prepare": sorted(result),
        "collections": sorted(c.name for c in scene.collection.children),
        "skybrush_version": scene.skybrush.version,
    }


def grid_positions(z: float) -> np.ndarray:
    xs, ys = np.meshgrid(np.arange(GRID) * SPACING_M, np.arange(GRID) * SPACING_M, indexing="ij")
    flat = np.column_stack([xs.ravel(), ys.ravel(), np.full(GRID * GRID, z)])
    return flat - np.array([flat[:, 0].mean(), flat[:, 1].mean(), 0.0])


def create_drones() -> dict[str, Any]:
    from sbstudio.plugin.constants import Collections, Templates
    from sbstudio.plugin.operators.create_takeoff_grid import create_drone

    template = Templates.find_drone()
    collection = Collections.find_drones()
    for i, location in enumerate(grid_positions(0.0)):
        create_drone(
            tuple(location), name=f"Drone {i + 1}", template=template, collection=collection
        )
    return {
        "drone_count": len(collection.objects),
        "template": template.name,
        "template_materials": [m.name for m in template.data.materials if m],
    }


def trajectories(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rise to 6 m, then rotate the grid around Z; colours fade from white to a hue ramp."""
    t = (frames - frames[0]) / FPS
    base = grid_positions(0.0)
    n = len(base)
    rise = np.clip(t / 2.0, 0.0, 1.0)
    angle = np.clip(t - 2.0, 0.0, None) * math.radians(30)
    cos, sin = np.cos(angle)[:, None], np.sin(angle)[:, None]
    positions = np.empty((len(frames), n, 3))
    positions[:, :, 0] = cos * base[None, :, 0] - sin * base[None, :, 1]
    positions[:, :, 1] = sin * base[None, :, 0] + cos * base[None, :, 1]
    positions[:, :, 2] = 6.0 * rise[:, None]
    hue = np.linspace(0.0, 1.0, n, endpoint=False)
    target = np.column_stack([0.5 + 0.5 * np.cos(2 * np.pi * (hue + k / 3)) for k in range(3)])
    mix = rise[:, None, None]
    colors = (1 - mix) * np.ones((1, n, 3)) + mix * target[None]
    return positions, colors


def write_keys(fcurve: Any, frames: np.ndarray, values: np.ndarray) -> None:
    points = fcurve.keyframe_points
    points.add(len(frames))
    co = np.empty(2 * len(frames), dtype=np.float32)
    co[0::2], co[1::2] = frames, values
    points.foreach_set("co", co)
    points.foreach_set("interpolation", np.full(len(frames), INTERPOLATION_LINEAR, dtype=np.int32))
    fcurve.update()


def keyframe_drones(frames: np.ndarray) -> dict[str, Any]:
    from sbstudio.plugin.constants import Collections

    drones = sorted(Collections.find_drones().objects, key=lambda o: int(o.name.split()[-1]))
    positions, colors = trajectories(frames)
    keys = 0
    for i, drone in enumerate(drones):
        drone.animation_data_create()
        action = bpy.data.actions.new(f"{drone.name} action")
        drone.animation_data.action = action
        for axis in range(3):
            write_keys(
                action.fcurve_ensure_for_datablock(drone, "location", index=axis),
                frames,
                positions[:, i, axis],
            )
        for channel in range(4):
            values = colors[:, i, channel] if channel < 3 else np.ones(len(frames))
            write_keys(
                action.fcurve_ensure_for_datablock(drone, "color", index=channel), frames, values
            )
        keys += 7 * len(frames)
    return {"drones": len(drones), "keyframes": keys, "slots": len(bpy.data.actions[0].slots)}


def add_storyboard(frame_end: int) -> dict[str, Any]:
    from sbstudio.plugin.model.formation import create_formation
    from sbstudio.plugin.model.storyboard import StoryboardEntryPurpose, get_storyboard

    storyboard = get_storyboard(context=bpy.context)
    layout = [
        ("Takeoff grid", StoryboardEntryPurpose.TAKEOFF, 1, 1, grid_positions(0.0)),
        (
            "Show",
            StoryboardEntryPurpose.SHOW,
            2 * FPS,
            frame_end - 2 * FPS - 10,
            grid_positions(6.0),
        ),
        ("Landing", StoryboardEntryPurpose.LANDING, frame_end - 5, 5, grid_positions(0.0)),
    ]
    for name, purpose, start, duration, points in layout:
        formation = create_formation(name, [tuple(p) for p in points])
        storyboard.add_new_entry(name, start, duration, purpose=purpose, formation=formation)
    for entry in storyboard.entries:
        entry.transition_type = "MANUAL"  # never let recalculate transitions touch our keys
    return {
        "entries": [
            {
                "name": e.name,
                "purpose": e.purpose,
                "frame_start": e.frame_start,
                "duration": e.duration,
            }
            for e in storyboard.entries
        ]
    }


def add_sound(wav_path: Path) -> dict[str, Any]:
    scene = bpy.context.scene
    editor = scene.sequence_editor_create()
    strips = getattr(editor, "strips", None)
    api = "strips"
    if strips is None:
        strips, api = editor.sequences, "sequences"
    strip = strips.new_sound("music", str(wav_path), channel=1, frame_start=1)
    return {
        "api": api,
        "strip_type": strip.type,
        "frame_final_duration": strip.frame_final_duration,
    }


def save(path: Path) -> dict[str, Any]:
    bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=True)
    return {"path": str(path), "bytes": path.stat().st_size}


def reopen(path: Path) -> dict[str, Any]:
    bpy.ops.wm.open_mainfile(filepath=str(path))
    from sbstudio.plugin.constants import Collections

    scene = bpy.context.scene
    drones = Collections.find_drones(create=False)
    return {
        "fps": scene.render.fps,
        "frame_range": [scene.frame_start, scene.frame_end],
        "drones": len(drones.objects) if drones else 0,
        "storyboard_entries": len(scene.skybrush.storyboard.entries),
        "skybrush_version": scene.skybrush.version,
        "sound_strips": len(scene.sequence_editor.strips) if scene.sequence_editor else 0,
    }


def sample_and_safety(frames_to_check: list[int]) -> dict[str, Any]:
    from sbstudio.plugin.constants import Collections

    scene = bpy.context.scene
    check = scene.skybrush.safety_check
    drones = sorted(Collections.find_drones().objects, key=lambda o: int(o.name.split()[-1]))
    all_frames = np.arange(scene.frame_start, scene.frame_end + 1)
    expected, expected_colors = trajectories(all_frames.astype(float))
    samples = []
    for frame in range(
        scene.frame_start, scene.frame_end + 1
    ):  # in order: the check caches 5 frames
        scene.frame_set(frame)
        if frame not in frames_to_check:
            continue
        idx = frame - scene.frame_start
        actual = np.array([d.matrix_world.translation[:] for d in drones])
        colors = np.array([d.color[:3] for d in drones])
        samples.append(
            {
                "frame": frame,
                "max_position_error_m": float(np.abs(actual - expected[idx]).max()),
                "max_color_error": float(np.abs(colors - expected_colors[idx]).max()),
                "safety_enabled": check.enabled,
                "min_distance": check.min_distance,
                "max_altitude": check.max_altitude,
                "max_velocity_xy": check.max_velocity_xy,
                "max_velocity_z_up": check.max_velocity_z_up,
                "max_velocity_z_down": check.max_velocity_z_down,
                "max_acceleration": check.max_acceleration,
            }
        )
    return {"samples": samples}


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--addon", default="ui_skybrush_studio")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    bpy.context.preferences.use_preferences_save = False
    probe = Probe()
    frame_end = SECONDS * FPS
    frames = np.arange(1, frame_end + 1, dtype=float)
    wav = out / "probe_music.wav"
    blend = out / "probe.blend"

    probe.step("environment", environment)
    probe.step("empty_homefile", lambda: sorted(bpy.ops.wm.read_homefile(use_empty=True)))
    probe.step("enable_addon", lambda: enable_addon(args.addon))
    probe.step("setup_scene", lambda: setup_scene(frame_end))
    probe.step("create_drones", create_drones)
    probe.step("keyframe_drones", lambda: keyframe_drones(frames))
    probe.step("storyboard", lambda: add_storyboard(frame_end))
    probe.step("write_wav", lambda: write_test_wav(wav))
    probe.step("sound_strip", lambda: add_sound(wav))
    probe.step("save", lambda: save(blend))
    probe.step("reopen", lambda: reopen(blend))
    probe.step(
        "sample_and_safety", lambda: sample_and_safety([1, FPS, 2 * FPS, 3 * FPS, frame_end])
    )

    report = {"ok": all(s["ok"] for s in probe.steps), "steps": probe.steps}
    (out / "probe_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[probe] report: {out / 'probe_report.json'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""Builds a Skybrush Studio show in background Blender from precomputed keyframes.

Runs inside Blender (stdlib + numpy + bpy + sbstudio only); see docs/SKYBRUSH_NOTES.md.
Input: ``--args args.json`` with ``keys_npz``, ``out_blend``, ``result_json``, ``fps``,
``frame_count``, ``drone_count``, ``addon``, ``safety``, ``storyboard``, ``audio``.
Frames in the inputs are zero-based; Blender frame = frame + 1.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import addon_utils
import bpy
import numpy as np

LINEAR = 1  # Keyframe.interpolation enum index


def write_keys(fcurve: Any, frames: np.ndarray, values: np.ndarray) -> None:
    points = fcurve.keyframe_points
    points.add(len(frames))
    co = np.empty(2 * len(frames), dtype=np.float32)
    co[0::2], co[1::2] = frames + 1, values
    points.foreach_set("co", co)
    points.foreach_set("interpolation", np.full(len(frames), LINEAR, dtype=np.int32))
    fcurve.update()


def build(args: dict[str, Any]) -> dict[str, Any]:
    bpy.context.preferences.use_preferences_save = False
    bpy.ops.wm.read_homefile(use_empty=True)
    module = args["addon"]
    if not addon_utils.check(module)[1]:
        addon_utils.enable(module, default_set=False, handle_error=None)
    from sbstudio.plugin.constants import Collections, Templates
    from sbstudio.plugin.model.formation import create_formation
    from sbstudio.plugin.model.storyboard import StoryboardEntryPurpose, get_storyboard
    from sbstudio.plugin.operators.create_takeoff_grid import create_drone

    data = np.load(args["keys_npz"])
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = int(args["fps"]), 1.0
    scene.frame_start, scene.frame_end = 1, int(args["frame_count"])
    scene.skybrush.version = 2
    bpy.ops.skybrush.prepare()

    safety = scene.skybrush.safety_check
    limits = args["safety"]
    safety.proximity_warning_threshold = limits["min_distance_m"]
    safety.altitude_warning_threshold = limits["altitude_max_m"]
    safety.velocity_xy_warning_threshold = limits["max_speed_h_ms"]
    safety.velocity_z_warning_threshold = limits["max_speed_down_ms"]
    safety.velocity_z_warning_different_up = True
    safety.velocity_z_warning_threshold_up = limits["max_speed_up_ms"]
    safety.acceleration_warning_threshold = limits["max_accel_ms2"]
    scene.skybrush.settings.max_acceleration = limits["max_accel_ms2"]

    template = Templates.find_drone()
    collection = Collections.find_drones()
    loc_off, col_off = data["loc_offsets"], data["col_offsets"]
    started = time.perf_counter()
    for drone in range(int(args["drone_count"])):
        lo, hi = loc_off[drone], loc_off[drone + 1]
        frames, values = data["loc_frames"][lo:hi], data["loc_values"][lo:hi]
        obj = create_drone(
            tuple(values[0]), name=f"Drone {drone + 1}", template=template, collection=collection
        )
        obj.animation_data_create()
        action = bpy.data.actions.new(f"Drone {drone + 1}")
        obj.animation_data.action = action
        for axis in range(3):
            write_keys(
                action.fcurve_ensure_for_datablock(obj, "location", index=axis),
                frames,
                values[:, axis],
            )
        lo, hi = col_off[drone], col_off[drone + 1]
        frames, values = data["col_frames"][lo:hi], data["col_values"][lo:hi]
        for channel in range(3):
            write_keys(
                action.fcurve_ensure_for_datablock(obj, "color", index=channel),
                frames,
                values[:, channel],
            )
        write_keys(
            action.fcurve_ensure_for_datablock(obj, "color", index=3), frames[:1], np.ones(1)
        )
    keyframe_seconds = time.perf_counter() - started

    storyboard = get_storyboard(context=bpy.context)
    for entry in args["storyboard"]:
        formation = create_formation(
            entry["name"], [tuple(p) for p in data[entry["positions_key"]]]
        )
        storyboard.add_new_entry(
            entry["name"],
            int(entry["frame_start"]) + 1,
            max(1, int(entry["duration"])),
            purpose=StoryboardEntryPurpose[entry["purpose"]],
            formation=formation,
        )
    for entry in storyboard.entries:
        entry.transition_type = "MANUAL"  # recalculation would override our keys

    audio = args.get("audio")
    if audio:
        editor = scene.sequence_editor_create()
        editor.strips.new_sound(
            "music", audio["path"], channel=1, frame_start=int(audio["frame_start"]) + 1
        )

    out = Path(args["out_blend"])
    out.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(out), compress=True)

    # Verification on the saved scene: sampled frames against the compiled trajectories.
    expected = data["verify_positions"]
    drones = sorted(collection.objects, key=lambda o: int(o.name.split()[-1]))
    verify = []
    for frame, positions in zip(data["verify_frames"], expected, strict=True):
        scene.frame_set(int(frame) + 1)
        actual = np.array([d.matrix_world.translation[:] for d in drones])
        verify.append(
            {
                "frame": int(frame),
                "max_error_m": float(np.abs(actual - positions).max()),
                "skybrush_min_distance_m": float(safety.min_distance),
            }
        )
    return {
        "ok": True,
        "blender_version": bpy.app.version_string,
        "addon_version": list(sys.modules[module].bl_info["version"]),
        "drones": len(drones),
        "keys": int(len(data["loc_frames"]) * 3 + len(data["col_frames"]) * 3),
        "keyframe_seconds": round(keyframe_seconds, 3),
        "storyboard": [
            {
                "name": e.name,
                "purpose": e.purpose,
                "frame_start": e.frame_start,
                "duration": e.duration,
            }
            for e in storyboard.entries
        ],
        "blend": str(out),
        "blend_bytes": out.stat().st_size,
        "verify": verify,
        "max_error_m": max((v["max_error_m"] for v in verify), default=0.0),
    }


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--args", required=True)
    args = json.loads(Path(parser.parse_args(argv).args).read_text(encoding="utf-8"))
    try:
        result = build(args)
    except Exception as exc:
        traceback.print_exc()
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    Path(args["result_json"]).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

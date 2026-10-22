"""Opens a built .blend in background Blender and reports what Skybrush Studio sees.

Input: ``--args args.json`` with ``blend``, ``result_json``, ``addon`` and ``frames``
(zero-based frames whose drone positions to report).
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

import addon_utils
import bpy


def inspect(args: dict[str, Any]) -> dict[str, Any]:
    bpy.context.preferences.use_preferences_save = False
    module = args["addon"]
    if not addon_utils.check(module)[1]:
        addon_utils.enable(module, default_set=False, handle_error=None)
    bpy.ops.wm.open_mainfile(filepath=args["blend"])
    from sbstudio.plugin.constants import Collections

    scene = bpy.context.scene
    drones = Collections.find_drones(create=False)
    objects = sorted(drones.objects, key=lambda o: int(o.name.split()[-1])) if drones else []
    positions = {}
    for frame in args.get("frames", []):
        scene.frame_set(int(frame) + 1)
        positions[str(frame)] = [list(o.matrix_world.translation) for o in objects]
    safety = scene.skybrush.safety_check
    editor = scene.sequence_editor
    return {
        "ok": True,
        "fps": scene.render.fps,
        "frame_range": [scene.frame_start, scene.frame_end],
        "drones": len(objects),
        "drone_names": [o.name for o in objects[:3]],
        "skybrush_version": scene.skybrush.version,
        "storyboard": [
            {
                "name": e.name,
                "purpose": e.purpose,
                "frame_start": e.frame_start,
                "transition": e.transition_type,
            }
            for e in scene.skybrush.storyboard.entries
        ],
        "sound_strips": [s.name for s in editor.strips] if editor else [],
        "safety": {
            "proximity_warning_threshold": safety.proximity_warning_threshold,
            "velocity_xy_warning_threshold": safety.velocity_xy_warning_threshold,
            "acceleration_warning_threshold": safety.acceleration_warning_threshold,
        },
        "positions": positions,
    }


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--args", required=True)
    args = json.loads(Path(parser.parse_args(argv).args).read_text(encoding="utf-8"))
    try:
        result = inspect(args)
    except Exception as exc:
        traceback.print_exc()
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    Path(args["result_json"]).write_text(json.dumps(result), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

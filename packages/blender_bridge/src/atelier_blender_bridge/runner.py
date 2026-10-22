"""Running scripts in headless Blender: ``blender -b --python script -- --args args.json``."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).parent / "scripts"


class BlenderError(Exception):
    pass


def _tail(path: Path, lines: int = 30) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        proc.kill()
    proc.wait(timeout=10)


def run_blender(
    blender_exe: Path,
    script: Path,
    args: dict[str, Any],
    *,
    workdir: Path,
    log_path: Path,
    timeout_s: float = 1800.0,
) -> dict[str, Any]:
    """Run ``script`` in background Blender and return the JSON it writes to ``result_json``.

    The script receives ``--args <file>``; ``args['result_json']`` is set here. Blender runs
    offline, with the user's add-ons, and never saves user preferences.
    """
    if not blender_exe.is_file():
        raise BlenderError(
            f"Blender not found at {blender_exe}; set paths.blender_exe in atelier.toml"
        )
    workdir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    result_path = workdir / "result.json"
    result_path.unlink(missing_ok=True)
    args_path = workdir / "args.json"
    args_path.write_text(
        json.dumps({**args, "result_json": str(result_path)}, indent=2), encoding="utf-8"
    )
    command = [
        str(blender_exe),
        "-b",
        "--offline-mode",
        "--python-exit-code",
        "1",
        "--python",
        str(script),
        "--",
        "--args",
        str(args_path),
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    with log_path.open("wb") as log:
        proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        try:
            code = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            raise BlenderError(
                f"Blender timed out after {timeout_s:.0f} s; log: {log_path}"
            ) from None
    if code != 0 or not result_path.is_file():
        raise BlenderError(f"Blender failed (exit code {code}); log: {log_path}\n{_tail(log_path)}")
    result: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
    if not result.get("ok", False):
        raise BlenderError(f"{result.get('error', 'Blender script failed')}; log: {log_path}")
    return result

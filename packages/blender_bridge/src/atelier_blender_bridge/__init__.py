"""Headless Blender runner and the scripts executed inside Blender."""

from importlib.metadata import version

from atelier_blender_bridge.keys import build_keys, decimate, save_keys
from atelier_blender_bridge.runner import SCRIPTS_DIR, BlenderError, run_blender

__version__ = version("atelier-blender-bridge")

__all__ = ["SCRIPTS_DIR", "BlenderError", "build_keys", "decimate", "run_blender", "save_keys"]

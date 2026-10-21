"""Run a plugin as a subprocess server::

python -m atelier_plugin_sdk.serve --entry my_plugin:plugin [--path src]
"""

from __future__ import annotations

import argparse
import importlib
import io
import sys
from collections.abc import Sequence
from pathlib import Path

from atelier_plugin_sdk.plugin import Plugin
from atelier_plugin_sdk.rpc.server import PluginServer, protect_stdout


def load_entry(entry: str) -> Plugin:
    module_name, _, attribute = entry.partition(":")
    plugin = getattr(importlib.import_module(module_name), attribute)
    if not isinstance(plugin, Plugin):
        raise TypeError(f"{entry} is {type(plugin).__name__}, expected atelier_plugin_sdk.Plugin")
    return plugin


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atelier_plugin_sdk.serve")
    parser.add_argument("--entry", required=True, help="module:attribute of the Plugin object")
    parser.add_argument("--path", action="append", default=[], help="extra import path")
    args = parser.parse_args(argv)
    out = protect_stdout()
    for path in reversed(args.path):
        sys.path.insert(0, str(Path(path).resolve()))
    plugin = load_entry(args.entry)
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", newline="\n")
    PluginServer(plugin, out).serve(stdin)
    return 0


if __name__ == "__main__":
    sys.exit(main())

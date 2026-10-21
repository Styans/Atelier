# plugins/

Every folder here is one plugin: an `atelier-plugin.toml` manifest plus a `src/` package.
The core never imports these packages; it reaches them only through the role contracts
of `atelier_plugin_sdk` (see `docs/ARCHITECTURE.md`, "Plugin contract").
`tests/test_architecture.py` enforces this.

Planned for phase 1: `pointille`, `builtin_animation`, `builtin_transitions`,
`builtin_validators`, `builtin_exporters`, `blender_skybrush`.
`pygmalion` (phase 2) runs as a subprocess plugin in its own venv and is excluded from
the uv workspace.

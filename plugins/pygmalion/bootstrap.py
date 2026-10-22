"""Starts the Pygmalion plugin with Pygmalion's own Python (ADR-026).

Nothing is installed into Pygmalion's venv: the host passes where the Atelier SDK lives
(``ATELIER_SDK_PATHS``) and the plugin settings (``ATELIER_PLUGIN_SETTINGS``). Pygmalion's
``server`` sources go first on the import path, ahead of the copy installed in its venv.
"""

import json
import logging
import os
import sys
from pathlib import Path

# Pygmalion reports model loading and job stages through logging; stderr ends up in the
# plugin log (.atelier/logs/pygmalion.log), stdout stays reserved for the protocol.
logging.basicConfig(
    level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

HERE = Path(__file__).resolve().parent
settings = json.loads(os.environ.get("ATELIER_PLUGIN_SETTINGS", "{}"))
sdk_paths = [p for p in os.environ.get("ATELIER_SDK_PATHS", "").split(os.pathsep) if p]
server_dir = [settings["server_dir"]] if settings.get("server_dir") else []
for path in reversed([*server_dir, str(HERE / "src"), *sdk_paths]):
    sys.path.insert(0, path)

from atelier_plugin_sdk.serve import main  # noqa: E402

sys.exit(main(sys.argv[1:]))

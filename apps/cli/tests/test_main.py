from typer.testing import CliRunner

from atelier_cli import __version__
from atelier_cli.main import app


def test_version_flag_prints_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"atelier {__version__}"

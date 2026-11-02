from pathlib import Path

from typer.testing import CliRunner

from atelier_cli import __version__
from atelier_cli.main import _git_pins, app


def test_version_flag_prints_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"atelier {__version__}"


def test_git_pins_are_read_from_the_lock(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        'version = 1\n\n[[package]]\nname = "numpy"\nversion = "2.1.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n\n'
        '[[package]]\nname = "pointille"\nversion = "0.1.0"\n'
        'source = { git = "https://github.com/Styans/Pointille?branch=main#ba5ead93dad5" }\n',
        encoding="utf-8",
    )

    assert _git_pins(lock) == {
        "pointille": ("https://github.com/Styans/Pointille", "main", "ba5ead93dad5")
    }

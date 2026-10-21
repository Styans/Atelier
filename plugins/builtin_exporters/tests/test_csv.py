import hashlib
import zipfile
from pathlib import Path

from atelier_builtin_exporters import CSV_HEADER, CsvParams, SkybrushCsv, plugin
from atelier_plugin_sdk import CompiledShowRef, RunContext
from atelier_plugin_sdk.testing import LocalInvoker, reference_compiled_show, run_contract_suite


def export(tmp_path: Path, out: str, params: CsvParams) -> Path:
    show = reference_compiled_show(drones=4, frames=31)
    ref = CompiledShowRef(path=show.save(tmp_path / "show.npz"))
    result = SkybrushCsv().export(RunContext(seed=0, workdir=tmp_path), ref, tmp_path / out, params)
    return result.files[0]


def test_one_file_per_drone_in_skybrush_format(tmp_path: Path) -> None:
    archive = export(tmp_path, "out", CsvParams())

    with zipfile.ZipFile(archive) as zf:
        names = sorted(zf.namelist())
        lines = zf.read("Drone 1.csv").decode().splitlines()

    assert names == ["Drone 1.csv", "Drone 2.csv", "Drone 3.csv", "Drone 4.csv"]
    assert lines[0] == CSV_HEADER
    assert len(lines) == 1 + 31
    assert lines[1] == "0,0.000,0.000,10.000,200,200,200"
    assert lines[-1].startswith("1000,")


def test_output_rate_can_be_lowered(tmp_path: Path) -> None:
    archive = export(tmp_path, "out", CsvParams(fps=10))

    with zipfile.ZipFile(archive) as zf:
        assert len(zf.read("Drone 1.csv").decode().splitlines()) == 1 + 11


def test_archive_is_byte_identical_between_runs(tmp_path: Path) -> None:
    first = hashlib.sha256(export(tmp_path, "a", CsvParams()).read_bytes()).hexdigest()
    second = hashlib.sha256(export(tmp_path, "b", CsvParams()).read_bytes()).hexdigest()

    assert first == second


def test_contract(tmp_path: Path) -> None:
    results = run_contract_suite(LocalInvoker(plugin.components[0]), tmp_path)

    assert all(r.ok for r in results), results

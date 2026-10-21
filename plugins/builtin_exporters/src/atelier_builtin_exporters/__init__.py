"""Built-in exporters."""

from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from atelier_plugin_sdk import CompiledShowRef, Exporter, ExportResult, Health, Plugin, RunContext

CSV_HEADER = "Time [msec],x [m],y [m],z [m],Red,Green,Blue"


class CsvParams(BaseModel):
    fps: int | None = Field(None, gt=0, description="Output rate; None = the show's fps")
    decimals: int = Field(3, ge=1, le=6, description="Decimals of the coordinates")


class SkybrushCsv(Exporter[CsvParams]):
    """Skybrush zipped CSV: one file per drone with time, position and LED colour.

    The format Skybrush Studio imports ("Add markers from zipped CSV"); the music file,
    if any, is copied next to it.
    """

    name = "csv"
    version = "0.1.0"
    Params = CsvParams

    def export(
        self, ctx: RunContext, show: CompiledShowRef, out_dir: Path, params: CsvParams
    ) -> ExportResult:
        compiled = show.load()
        out_fps = params.fps or compiled.fps
        times = np.arange(0.0, compiled.duration_s + 1e-9, 1.0 / out_fps)
        frames = np.minimum(
            np.round(times * compiled.fps).astype(np.int64), compiled.frame_count - 1
        )
        millis = np.round(times * 1000).astype(np.int64)
        out_dir.mkdir(parents=True, exist_ok=True)
        archive = out_dir / "skybrush_csv.zip"
        fmt = f"{{:.{params.decimals}f}}"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for drone in range(compiled.drone_count):
                pos = compiled.positions[frames, drone].astype(np.float64)
                col = compiled.colors[frames, drone]
                buffer = io.StringIO()
                buffer.write(CSV_HEADER + "\n")
                for ms, p, c in zip(millis, pos, col, strict=True):
                    coords = ",".join(fmt.format(v) for v in p)
                    buffer.write(f"{ms},{coords},{c[0]},{c[1]},{c[2]}\n")
                # Fixed timestamp keeps the archive byte-identical between runs.
                info = zipfile.ZipInfo(f"Drone {drone + 1}.csv", date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, buffer.getvalue())
                if drone % 50 == 0:
                    ctx.progress(drone / compiled.drone_count, "writing CSV")
        files = [archive]
        if compiled.audio is not None and compiled.audio.path.is_file():
            audio = out_dir / f"music{compiled.audio.path.suffix}"
            shutil.copyfile(compiled.audio.path, audio)
            files.append(audio)
        return ExportResult(
            files=files,
            meta={"drones": compiled.drone_count, "fps": out_fps, "samples": len(times)},
        )


plugin = Plugin(components=[SkybrushCsv()], health_check=Health.ok)

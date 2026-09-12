"""Elaborate Verilog with Yosys and load the JSON netlist.

Yosys attaches a `src` attribute ("file.v:LINE.COL-LINE.COL") to every cell and
wire it creates, which is what lets us map source lines to graph nodes.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile


class SynthError(RuntimeError):
    pass


def yosys_available() -> bool:
    return shutil.which("yosys") is not None


def elaborate(verilog: str | pathlib.Path, top: str, *, flatten: bool = True, defines: list[str] | None = None) -> dict:
    """Run Yosys on one Verilog file and return the parsed JSON netlist.

    flatten=True gives a single module with the whole design (used for the graph).
    flatten=False keeps the hierarchy (used for instances and ports in the export).
    """
    if not yosys_available():
        raise SynthError("yosys not found on PATH (brew install yosys)")
    defs = " ".join(f"-D{d}" for d in (defines or []))
    steps = [f"read_verilog {defs} {verilog}", f"hierarchy -top {top}", "proc"]
    if flatten:
        steps += ["flatten", "opt_clean"]
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "design.json"
        steps.append(f"write_json {out}")
        r = subprocess.run(["yosys", "-q", "-p", "; ".join(steps)], capture_output=True, text=True)
        if r.returncode != 0:
            errs = [l for l in (r.stdout + r.stderr).splitlines() if "ERROR" in l]
            raise SynthError(errs[-1] if errs else "yosys failed")
        return json.loads(out.read_text())


_SRC_RE = re.compile(r"([^|:]+):(\d+)\.\d+-(\d+)\.\d+")


def src_lines(attrs: dict) -> set[int]:
    """Line numbers covered by a Yosys `src` attribute (may hold several ranges joined by '|')."""
    lines: set[int] = set()
    for m in _SRC_RE.finditer(attrs.get("src", "")):
        lines.update(range(int(m.group(2)), int(m.group(3)) + 1))
    return lines


def src_first_line(attrs: dict) -> tuple[str, int] | None:
    m = _SRC_RE.search(attrs.get("src", ""))
    if not m:
        return None
    return pathlib.Path(m.group(1)).name, int(m.group(2))

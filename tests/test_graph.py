import pathlib
import subprocess

import pytest

from rtl_impact import graph, identity, gitrev, tofexport, yosys

DATA = pathlib.Path(__file__).parent / "data"
pytestmark = pytest.mark.skipif(not yosys.yosys_available(), reason="yosys not installed")


def load(name, defines=None):
    return graph.build(yosys.elaborate(DATA / name, "pipe", defines=defines), "pipe")


def test_summary_counts_registers_and_no_loops():
    d = load("pipe.v")
    s = graph.summary(d)
    assert s["registers"] == 3          # s1, s2, y (one $adff each after proc)
    assert s["combinational_loops"] == 0
    assert s["cells"] >= 6              # 3 regs + add + xor + add


def test_same_cycle_cone_stops_at_registers():
    d = load("pipe.v")
    seeds = d.bits_of_port("y")
    same = graph.cone_report(d, graph.backward_cone(d, seeds, same_cycle=True))
    trans = graph.cone_report(d, graph.backward_cone(d, seeds, same_cycle=False))
    assert same["cells"] < trans["cells"]
    assert trans["cells"] == len(d.G.cells())   # everything eventually feeds y


def test_seeds_from_lines_ignore_code_that_is_not_compiled():
    d = load("pipe.v")
    ifdef_lines = {17, 18}                      # inside `ifdef EXTRA_DEBUG
    assert graph.seeds_from_lines(d, ifdef_lines) == set()
    d2 = load("pipe.v", defines=["EXTRA_DEBUG"])
    assert graph.seeds_from_lines(d2, ifdef_lines)


def test_rename_keeps_structural_identity():
    a, b = load("pipe.v"), load("pipe_renamed.v")
    r = identity.compare(a, b)
    assert r["unchanged"] == r["cells_after"] == r["cells_before"]


def test_constant_change_breaks_identity_locally():
    a, b = load("pipe.v"), load("pipe_const.v")
    r = identity.compare(a, b)
    assert 0 < r["unchanged"] < r["cells_after"]


def test_git_impact_end_to_end(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "pipe.v").write_text((DATA / "pipe.v").read_text())
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "v1"], cwd=repo, check=True)
    (repo / "pipe.v").write_text((DATA / "pipe_const.v").read_text())
    subprocess.run(["git", "commit", "-q", "-am", "change constant"], cwd=repo, check=True)
    lines = gitrev.changed_lines(repo, "HEAD", "pipe.v")
    assert lines == {13}
    tmp = gitrev.scratch_dir()
    cur = graph.build(yosys.elaborate(gitrev.file_at(repo, "HEAD", "pipe.v", tmp), "pipe"), "pipe")
    seeds = graph.seeds_from_lines(cur, lines)
    assert seeds
    rep = graph.cone_report(cur, graph.forward_cone(cur, seeds, same_cycle=False))
    assert "y" in rep["output_ports"]


def test_tof_export_is_well_formed_and_parses_if_tof_installed():
    d = load("pipe.v")
    hier = yosys.elaborate(DATA / "pipe.v", "pipe", flatten=False)
    text = tofexport.export(d, hier, ip_name="pipe", revision="test", findings=[
        {"rule": "IMPACT", "status": "confirmed", "severity": "info", "title": "no change", "desc": "x", "reasons": ["a", "b"], "port": "y", "srcs": [("pipe.v", 13)]}
    ])
    assert text.startswith("tapeout 1\nip pipe revision test top pipe\n")
    assert "port y output [7:0]" in text
    ok, msg = tofexport.validate(text)
    assert ok or "not installed" in msg, msg

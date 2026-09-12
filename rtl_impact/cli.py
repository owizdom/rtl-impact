"""Command line interface.

  rtl-impact stats    DESIGN.v --top TOP
  rtl-impact cone     DESIGN.v --top TOP --port NAME [--transitive]
  rtl-impact impact   --repo DIR --file PATH --top TOP --commit SHA
  rtl-impact identity --repo DIR --file PATH --top TOP --commit SHA [--rounds K]
  rtl-impact report   --repo DIR --file PATH --top TOP --commits SHA... [--json OUT] [--tapeout OUT]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import graph, identity, gitrev, tofexport, yosys


def _load(verilog, top, defines=None) -> graph.Design:
    return graph.build(yosys.elaborate(verilog, top, defines=defines), top)


def cmd_stats(a):
    d = _load(a.design, a.top, a.define)
    print(json.dumps(graph.summary(d), indent=2))


def cmd_cone(a):
    d = _load(a.design, a.top, a.define)
    if a.port not in d.ports:
        sys.exit(f"no port named {a.port}; ports: {', '.join(d.ports)}")
    seeds = d.bits_of_port(a.port)
    same = graph.cone_report(d, graph.backward_cone(d, seeds, same_cycle=True))
    trans = graph.cone_report(d, graph.backward_cone(d, seeds, same_cycle=False))
    total = len(d.G.cells())
    print(json.dumps({"port": a.port, "bits": len(seeds), "total_cells": total, "same_cycle": same, "transitive": trans}, indent=2))


def _impact_for_commit(repo, path, top, commit, rounds, tmp):
    lines = gitrev.changed_lines(repo, commit, path)
    cur = graph.build(yosys.elaborate(gitrev.file_at(repo, commit, path, tmp), top), top)
    seeds = graph.seeds_from_lines(cur, lines)
    same = graph.cone_report(cur, graph.forward_cone(cur, seeds, same_cycle=True))
    trans = graph.cone_report(cur, graph.forward_cone(cur, seeds, same_cycle=False))
    prev = graph.build(yosys.elaborate(gitrev.file_at(repo, f"{commit}^", path, tmp), top), top)
    ident = identity.compare(prev, cur, rounds)
    total = len(cur.G.cells())
    return {
        "commit": gitrev.short(repo, commit), "subject": gitrev.subject(repo, commit),
        "changed_lines": len(lines), "seed_nodes": len(seeds), "total_cells": total,
        "same_cycle": same, "transitive": trans, "identity": ident,
    }, cur


def cmd_impact(a):
    tmp = gitrev.scratch_dir()
    r, _ = _impact_for_commit(a.repo, a.file, a.top, a.commit, a.rounds, tmp)
    print(json.dumps(r, indent=2))


def cmd_identity(a):
    tmp = gitrev.scratch_dir()
    cur = graph.build(yosys.elaborate(gitrev.file_at(a.repo, a.commit, a.file, tmp), a.top), a.top)
    prev = graph.build(yosys.elaborate(gitrev.file_at(a.repo, f"{a.commit}^", a.file, tmp), a.top), a.top)
    print(json.dumps({"commit": gitrev.short(a.repo, a.commit), **identity.compare(prev, cur, a.rounds)}, indent=2))


def cmd_report(a):
    tmp = gitrev.scratch_dir()
    head = gitrev.short(a.repo, "HEAD")
    head_v = gitrev.file_at(a.repo, "HEAD", a.file, tmp)
    d = graph.build(yosys.elaborate(head_v, a.top), a.top)
    hier = yosys.elaborate(head_v, a.top, flatten=False)
    out = {"repo": str(a.repo), "file": a.file, "top": a.top, "head": head, "summary": graph.summary(d), "cones": {}, "commits": []}
    for p, (dirn, bits) in d.ports.items():
        if dirn != "output":
            continue
        seeds = d.bits_of_port(p)
        out["cones"][p] = {
            "bits": len(seeds),
            "same_cycle_cells": graph.cone_report(d, graph.backward_cone(d, seeds, same_cycle=True))["cells"],
            "transitive_cells": graph.cone_report(d, graph.backward_cone(d, seeds, same_cycle=False))["cells"],
        }
    findings = []
    for c in a.commits:
        try:
            r, _ = _impact_for_commit(a.repo, a.file, a.top, c, a.rounds, tmp)
        except yosys.SynthError as e:
            out["commits"].append({"commit": c, "skipped": str(e)})
            continue
        out["commits"].append(r)
        ident = r["identity"]
        changed = ident["changed_or_new"]
        sev = "warning" if changed else "info"
        title = f"{r['commit']} {r['subject']}"[:90]
        desc = (f"{r['changed_lines']} changed lines seed {r['seed_nodes']} nodes; same-cycle cone {r['same_cycle']['cells']} cells, "
                f"transitive {r['transitive']['cells']} of {r['total_cells']}; structural identity unchanged for "
                f"{ident['unchanged']} of {ident['cells_after']} cells (k={ident['rounds']})")
        findings.append({"rule": "IMPACT", "status": "confirmed", "severity": sev, "title": title, "desc": desc,
                         "reasons": ["line_overlap_seed", "wl_structural_hash"],
                         "port": (r["same_cycle"]["output_ports"] or [None])[0],
                         "srcs": [(pathlib.Path(a.file).name, min(gitrev.changed_lines(a.repo, c, a.file)))] if r["changed_lines"] else []})
    text = tofexport.export(d, hier, ip_name=pathlib.Path(a.file).stem, revision=head, findings=findings)
    ok, msg = tofexport.validate(text)
    out["tapeout_validation"] = msg
    if a.tapeout:
        pathlib.Path(a.tapeout).write_text(text)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "cones"}, indent=2))
    print(f"tapeout: {msg}", file=sys.stderr)


def main(argv=None):
    p = argparse.ArgumentParser(prog="rtl-impact", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stats"); s.add_argument("design"); s.add_argument("--top", required=True); s.add_argument("--define", action="append"); s.set_defaults(fn=cmd_stats)
    s = sub.add_parser("cone"); s.add_argument("design"); s.add_argument("--top", required=True); s.add_argument("--port", required=True); s.add_argument("--define", action="append"); s.set_defaults(fn=cmd_cone)
    for name, fn in [("impact", cmd_impact), ("identity", cmd_identity)]:
        s = sub.add_parser(name); s.add_argument("--repo", required=True); s.add_argument("--file", required=True); s.add_argument("--top", required=True)
        s.add_argument("--commit", required=True); s.add_argument("--rounds", type=int, default=3); s.set_defaults(fn=fn)
    s = sub.add_parser("report"); s.add_argument("--repo", required=True); s.add_argument("--file", required=True); s.add_argument("--top", required=True)
    s.add_argument("--commits", nargs="+", required=True); s.add_argument("--rounds", type=int, default=3)
    s.add_argument("--json"); s.add_argument("--tapeout"); s.set_defaults(fn=cmd_report)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except yosys.SynthError as e:
        sys.exit(f"yosys: {e}")


if __name__ == "__main__":
    main()

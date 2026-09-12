"""Emit a Tapeout Object Format (.tapeout) document from a design and a set of impact results.

Statements used: ip, module, port, param, instance, finding. Grammar: tapeout-labs/tof, format version 1.
Every fact carries an `@ file:line` where Yosys gave us one.
"""
from __future__ import annotations

from .graph import Design


def _q(s: str) -> str:
    return '"' + s.replace('"', "'") + '"'


def _src(loc) -> str:
    return f" @ {loc[0]}:{loc[1]}" if loc else ""


def export(design: Design, hier_json: dict, *, ip_name: str, revision: str, findings: list[dict]) -> str:
    top = design.top
    mod = hier_json["modules"][top]
    lines = ["tapeout 1", f"ip {ip_name} revision {revision} top {top}", ""]
    out_ports = [p for p, (d, _) in design.ports.items()]
    lines.append(f"module {top} ports {len(out_ports)}")
    for pname, (direction, bits) in design.ports.items():
        width = f" [{len(bits) - 1}:0]" if len(bits) > 1 else ""
        lines.append(f"port {pname} {direction}{width}{_src(design.port_src.get(pname))}")
    lines.append("")
    for pname, pval in mod.get("parameter_default_values", {}).items():
        val = str(pval).strip()
        if not val or any(ch.isspace() for ch in val):
            val = _q(val)
        lines.append(f"param {pname} value {val}")
    lines.append("")
    from .yosys import src_first_line
    for cname, c in mod["cells"].items():
        ctype = c["type"]
        if ctype.startswith("$paramod"):            # Yosys names parameterized instances "$paramod\<module>\PARAM=..." or "$paramod$<hash>\<module>"
            parts = ctype.split("\\")
            ctype = parts[1] if len(parts) > 1 else ctype
        if ctype.startswith("$"):
            continue                                 # a primitive cell, not a module instance
        lines.append(f"instance {cname} of {ctype}{_src(src_first_line(c.get('attributes', {})))}")
    lines.append("")
    for i, f in enumerate(findings, 1):
        rule = f.get("rule", "IMPACT")
        status = f.get("status", "confirmed")
        sev = f.get("severity", "info")
        s = f"finding {rule}-{i:03d} {status} {sev} {_q(f['title'])}"
        if f.get("port"):
            s += f" port {f['port']}"
        if f.get("confidence") is not None:
            s += f" confidence {f['confidence']:.2f}"
        if f.get("reasons"):
            s += " reasons " + ",".join(f["reasons"])
        if f.get("desc"):
            s += f" desc {_q(f['desc'])}"
        for loc in f.get("srcs", []):
            s += _src(loc)
        lines.append(s)
    return "\n".join(lines) + "\n"


def validate(text: str) -> tuple[bool, str]:
    """Parse with the reference parser if it is installed (pip install ./tof from tapeout-labs/tof)."""
    try:
        from tof import parse_tapeout  # type: ignore
    except ImportError:
        return False, "tof parser not installed; skipped validation"
    try:
        doc = parse_tapeout(text)
    except Exception as e:  # TofParseError is a ValueError subclass
        return False, f"parse error: {e}"
    n = len(getattr(doc, "findings", []) or [])
    return True, f"parsed by tof reference parser ({n} findings)"

"""The signal-level graph: net bits and cells, with source lines on every node."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .yosys import src_lines

REG_TYPES = {
    "$dff", "$adff", "$dffe", "$adffe", "$sdff", "$sdffe", "$sdffce", "$dffsr", "$dffsre",
    "$aldff", "$aldffe", "$dlatch", "$adlatch", "$mem", "$mem_v2", "$memrd", "$memrd_v2", "$memwr", "$memwr_v2",
}


class DiGraph:
    """Minimal directed graph. Nodes carry attribute dicts; edges carry a port name."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.succ: dict[str, set[str]] = {}
        self.pred: dict[str, set[str]] = {}
        self.edge_port: dict[tuple[str, str], str] = {}

    def add_node(self, n: str, **attrs) -> None:
        if n not in self.nodes:
            self.nodes[n] = {}
            self.succ[n] = set()
            self.pred[n] = set()
        self.nodes[n].update(attrs)

    def add_edge(self, u: str, v: str, port: str = "") -> None:
        self.add_node(u)
        self.add_node(v)
        self.succ[u].add(v)
        self.pred[v].add(u)
        self.edge_port[(u, v)] = port

    def __contains__(self, n: str) -> bool:
        return n in self.nodes

    def number_of_edges(self) -> int:
        return len(self.edge_port)

    def cells(self) -> list[str]:
        return [n for n, d in self.nodes.items() if d.get("kind") == "cell"]

    def is_reg(self, n: str) -> bool:
        return bool(self.nodes[n].get("reg"))


@dataclass
class Design:
    top: str
    G: DiGraph
    net_names: dict[int, list[tuple[str, set[int]]]]  # bit id -> [(net name, source lines)]
    ports: dict[str, tuple[str, list[int]]]  # port name -> (direction, bit ids)
    port_src: dict[str, tuple[str, int] | None] = field(default_factory=dict)

    def bits_of_port(self, name: str) -> set[str]:
        return {f"n:{b}" for b in self.ports[name][1] if f"n:{b}" in self.G}


def build(design_json: dict, top: str) -> Design:
    mod = design_json["modules"][top]
    G = DiGraph()
    for cname, c in mod["cells"].items():
        consts = sorted(
            f"{port}[{i}]={b}" for port, bits in c["connections"].items() for i, b in enumerate(bits) if isinstance(b, str)
        )
        G.add_node(
            "c:" + cname, kind="cell", ctype=c["type"], params=c.get("parameters", {}), consts=consts,
            lines=src_lines(c.get("attributes", {})), reg=c["type"] in REG_TYPES,
        )
        for port, bits in c["connections"].items():
            direction = c["port_directions"].get(port, "input")
            for b in bits:
                if isinstance(b, str):
                    continue
                n = f"n:{b}"
                G.add_node(n, kind="net")
                if direction == "output":
                    G.add_edge("c:" + cname, n, port)
                else:
                    G.add_edge(n, "c:" + cname, port)
    net_names: dict[int, list[tuple[str, set[int]]]] = {}
    for nname, nn in mod["netnames"].items():
        ls = src_lines(nn.get("attributes", {}))
        for b in nn["bits"]:
            if isinstance(b, int):
                net_names.setdefault(b, []).append((nname, ls))
    ports = {p: (d["direction"], [b for b in d["bits"] if isinstance(b, int)]) for p, d in mod["ports"].items()}
    from .yosys import src_first_line
    port_src = {p: src_first_line(mod["netnames"].get(p, {}).get("attributes", {})) for p in ports}
    return Design(top=top, G=G, net_names=net_names, ports=ports, port_src=port_src)


def summary(d: Design) -> dict:
    cells = d.G.cells()
    return {
        "top": d.top,
        "cells": len(cells),
        "registers": sum(1 for c in cells if d.G.is_reg(c)),
        "net_bits": sum(1 for n, a in d.G.nodes.items() if a.get("kind") == "net"),
        "edges": d.G.number_of_edges(),
        "cell_types": Counter(d.G.nodes[c]["ctype"] for c in cells).most_common(10),
        "combinational_loops": len(combinational_loops(d)),
    }


def seeds_from_lines(d: Design, lines: set[int]) -> set[str]:
    """Nodes whose source range overlaps the given line numbers."""
    hits = {c for c in d.G.cells() if d.G.nodes[c]["lines"] & lines}
    for b, names in d.net_names.items():
        if any(ls & lines for _, ls in names) and f"n:{b}" in d.G:
            hits.add(f"n:{b}")
    return hits


def _reach(succ: dict[str, set[str]], G: DiGraph, seeds: set[str], stop_at_regs: bool) -> set[str]:
    seen = set(seeds)
    stack = list(seeds)
    while stack:
        u = stack.pop()
        for v in succ[u]:
            if v in seen:
                continue
            seen.add(v)
            if stop_at_regs and G.is_reg(v):
                continue  # the register is in the cone; we do not cross it
            stack.append(v)
    return seen


def forward_cone(d: Design, seeds: set[str], *, same_cycle: bool) -> set[str]:
    return _reach(d.G.succ, d.G, seeds, same_cycle)


def backward_cone(d: Design, seeds: set[str], *, same_cycle: bool) -> set[str]:
    return _reach(d.G.pred, d.G, seeds, same_cycle)


def cone_report(d: Design, nodes: set[str]) -> dict:
    cells = [n for n in nodes if d.G.nodes[n].get("kind") == "cell"]
    outs = sorted(p for p, (dirn, bits) in d.ports.items() if dirn == "output" and any(f"n:{b}" in nodes for b in bits))
    return {"cells": len(cells), "registers": sum(1 for c in cells if d.G.is_reg(c)), "output_ports": outs}


def combinational_loops(d: Design) -> list[set[str]]:
    """Strongly connected components of size > 1 among non-register nodes (iterative Tarjan)."""
    nodes = {n for n, a in d.G.nodes.items() if not a.get("reg")}
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    onstack: set[str] = set()
    stack: list[str] = []
    out: list[set[str]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(d.G.succ[root]))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        onstack.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in nodes:
                    continue
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    onstack.add(w)
                    work.append((w, iter(d.G.succ[w])))
                    advanced = True
                    break
                if w in onstack:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = set()
                while True:
                    w = stack.pop()
                    onstack.discard(w)
                    comp.add(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    out.append(comp)
    return out

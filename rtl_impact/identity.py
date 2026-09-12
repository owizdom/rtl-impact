"""Structural node identity across revisions.

A Weisfeiler-Lehman style relabeling: a cell's label starts from its type, parameters
and constant inputs, then absorbs its neighbours' labels for k rounds. Names and source
positions are deliberately excluded, so a rename or a moved line keeps the identity and
a logic change within k hops breaks it.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from .graph import Design


def structural_ids(d: Design, rounds: int = 3) -> dict[str, str]:
    G = d.G
    lab: dict[str, str] = {}
    for n, a in G.nodes.items():
        if a.get("kind") == "cell":
            lab[n] = a["ctype"] + json.dumps(a.get("params", {}), sort_keys=True) + ";".join(a.get("consts", []))
        else:
            lab[n] = "net"
    for _ in range(rounds):
        new: dict[str, str] = {}
        for n in G.nodes:
            ins = sorted(f"{G.edge_port[(u, n)]}<{lab[u]}" for u in G.pred[n])
            outs = sorted(f"{G.edge_port[(n, v)]}>{lab[v]}" for v in G.succ[n])
            new[n] = hashlib.blake2b((lab[n] + "|" + ",".join(ins) + "|" + ",".join(outs)).encode(), digest_size=12).hexdigest()
        lab = new
    return {n: h for n, h in lab.items() if G.nodes[n].get("kind") == "cell"}


def compare(prev: Design, cur: Design, rounds: int = 3) -> dict:
    hp = Counter(structural_ids(prev, rounds).values())
    hc = Counter(structural_ids(cur, rounds).values())
    shared = sum((hp & hc).values())
    return {
        "rounds": rounds,
        "cells_before": sum(hp.values()),
        "cells_after": sum(hc.values()),
        "unchanged": shared,
        "changed_or_new": sum(hc.values()) - shared,
    }

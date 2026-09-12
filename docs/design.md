# Silicon Context Model: change-impact and identity

A design note to go with `rtl-impact`. Written after reading tapeoutlabs.com/llms.txt, the tof grammar and the two Tapeout Labs datasets. The numbers below are from the tool in this repository, run on PicoRV32 on 10 September 2026.

## 1. Summary

Tapeout Labs describes a persistent graph of the chip program and asks: given revision N and N+1, which simulation, assertion, coverage and formal evidence from N can safely survive into N+1. This note proposes the substrate that question needs: a graph schema with provenance and trust tiers on every fact, a three-layer identity scheme so a node keeps its evidence across revisions when its meaning did not change, an incremental ingestion pipeline, two impact queries, and a harness that scores all of it against real commits and injected bugs.

The tool in this repo shows the central effect. On a commit that only renamed a signal, line-based impact flags 80% of the design; structural identity says zero cells changed. On a real decode fix, 368 cells change identity and the rest keep theirs. On an edit under an `ifdef` that is not compiled, impact is zero.

## 2. Definitions

| Term | Meaning |
|---|---|
| Artifact | A file at a revision: RTL, register description, header, test, log, coverage file. |
| Fact | One statement a parser can make with a source location. |
| Tier | `confirmed`: minted by a deterministic parser with a source. `proposed`: from a model or heuristic, with a confidence, needs review. |
| Revision | A commit. Every node and edge has a valid-from and optional valid-to revision. |
| Evidence | A tool result at a revision, attached to nodes: a test pass or fail with seed, a coverage hit, a proof result. |
| Configuration | The parameter and define set the design was elaborated with. Impact is only defined per configuration. |

## 3. Schema

Layers and their node types:

- Spec: requirement, vplan item.
- Design: module, instance, port, signal or net bit, logic cell, register, field, assertion.
- Firmware: C define.
- Verification: test plus seed, coverage bin, failure, evidence record.
- History: commit, finding, decision.

Edges inside the design, firmware and verification layers are minted by parsers and are confirmed. The spec-to-module edge is proposed by default because no parser reads a requirements document deterministically.

Every node and edge carries: `src` (path, line range, artifact content hash), `tier`, `confidence` on proposed facts, `minted_by` (parser or model id and version), `valid_from` and `valid_to`, `config` on design-layer facts, and the identity keys below.

The tof format is a serialization of this: `module`, `port`, `register`, `field`, `assertion`, `vplan` are node lines; `bind` is an edge with a tier; `finding` is a history node; `@ path:line` is `src`. Two additive attributes would complete it: the artifact content hash next to the line, and the revision a fact is valid from.

## 4. Identity across revisions

Evidence attaches to a node. At N+1, "does this evidence still apply" reduces to "is this the same node". Three keys, each answering a different version of "same":

| Key | Computed from | Survives | Breaks on |
|---|---|---|---|
| `name_id` | hierarchical path | logic changes, line moves | renames, restructuring |
| `struct_id` | Weisfeiler-Lehman hash over type, parameters, constant inputs and neighbour labels, k rounds | renames, moves, comments | logic changes within k hops |
| `text_id` | hash of the normalized source of the defining construct | nothing else changing | any edit, including comments |

Rules: evidence attaches to `struct_id`; when `struct_id` is unchanged and `name_id` changed, record a rename edge, not a delete and insert; `text_id` is the cheap early exit; k is tuned by the harness.

## 5. Ingestion

1. Discover artifacts whose content hash changed between N and N+1.
2. Run parsers only on those. Parsers emit facts with `src`, never ids.
3. Resolve identity: compute the three keys, match against N by `struct_id` then `name_id`, emit add, retire or rename.
4. Append to a fact log with `valid_from = N+1`; retire sets `valid_to`.
5. Materialize the graph at N+1 as a delta.
6. Re-index incrementally (Pearce and Kelly for topological order; cone summaries only where neighbourhoods changed).
7. Accept evidence keyed by `struct_id`. Evidence whose target still exists and is outside every changed node's forward cone stays valid; the rest is marked stale, with the reason.

## 6. Queries

| Query | Algorithm | Output |
|---|---|---|
| Backward cone | reverse reachability, same-cycle stops at registers | cells and lines that can influence a value |
| Forward impact | forward reachability from seeds | affected cells, outputs, assertions, tests, bins; rerun set; stale evidence |
| Conflict finding | group confirmed facts by (node, attribute), flag disagreements | a `finding` with both sources cited |
| Memory lookup | signature match, then nearest by cone overlap | past findings and decisions |

Two refinements the prototype made obvious: seed at the statement level, not by line overlap; and treat the identity delta, not the transitive cone, as the rerun criterion, because on a CPU the transitive cone of almost anything is almost everything.

## 7. Harness

| Metric | How | Target |
|---|---|---|
| Rerun recall | run the full regression per commit or mutation; compare with the selected set | 1.0 on mutations |
| Rerun fraction | selected tests over all tests | well below 1.0 on small commits |
| Evidence survival precision | of evidence kept valid, how much really was | 1.0; keeping stale evidence is the unacceptable error |
| Evidence survival recall | of evidence really still valid, how much was kept | as high as k allows |
| Triage accuracy | historical failures with known root causes; score the node and the localization | per cluster, abstentions counted separately |

## 8. Storage

On-prem, no external services, history that compounds, queries that take a revision. Prototype: SQLite plus in-memory adjacency. Production shape: an append-only fact log as the source of truth, a materialized graph per revision in an embedded key-value store, single writer, many readers, evidence in the same log keyed by `struct_id`. A general graph database can sit on top later if a customer needs ad hoc queries.

## 9. Plan

| When | What | Done means |
|---|---|---|
| Week 1 | statement-level seeds via slang; per-test Verilator coverage edges on PicoRV32's suite; rerun-recall and evidence-survival on 10 real commits and 20 mutations | a results table with test and evidence columns |
| Week 2 | fact log with validity; incremental ingestion across a commit range; identity rules; tof export validated by the reference parser | 50 consecutive commits ingested without a rebuild |
| Weeks 3 to 6 | Ibex through slang; assertions as nodes; the HJSON register layer, firmware defines and bind edges; configurations | the same harness on Ibex plus conflict findings on OpenTitan register files |

## 10. Questions for Tapeout Labs

1. What is the graph stored in today, and what breaks first at OpenTitan scale?
2. How is node identity handled across revisions now?
3. Do the parsers elaborate per configuration or work on unelaborated source?
4. Is evidence already keyed to graph nodes, or to tests only?
5. Which of the six pipeline stages is the bottleneck?

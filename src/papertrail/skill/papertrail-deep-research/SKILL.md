---
name: papertrail-deep-research
description: Search and analyze a local PaperTrail scholarly corpus for literature reviews, prior-art maps, research gaps, novelty challenges, competing explanations, and new falsifiable research ideas. Use when the user asks to deeply research an academic topic, compare papers, inspect paper diagrams, validate a claim against literature, or discover promising research directions with exact evidence.
---

# PaperTrail Deep Research

Use PaperTrail as an evidence system, not as an oracle. Search broadly at the metadata level, reason deeply only from acquired full text, and make the boundary between those two levels explicit.

## Research workflow

1. Call `get_corpus_status`. State material coverage limits before making completeness or novelty claims.
2. Search the catalog with `search_catalog`. Run several queries covering the user's wording, synonyms, mechanisms, methods, benchmarks, and likely opposing terminology. Apply category and date filters when relevant.
3. Call `get_research_groups` when an organization run exists. Use its problem neighborhoods to broaden terminology and notice converging or fragmented lines of work; cluster membership is navigation, not evidence.
4. Build a candidate map from titles and abstracts. Treat any result whose `full_text_status` is not `acquired` as metadata-level leads, not as evidence for detailed claims.
5. Search acquired text with `search_papers` for exact evidence. Use `hybrid_search` when a configured embedding provider is available and conceptual recall matters.
6. Expand strong candidates with `get_paper`, `find_related_papers`, and `get_scientific_records`. Retrieve cited passages with `get_evidence`; do not cite an extracted record without checking its evidence.
7. Search visual evidence with `search_figures`, then call `get_figure` to inspect the rendered page when architecture, algorithms, plots, or result tables matter. A page render is not automatically a correctly interpreted diagram.
8. Actively search for counterexamples, negative results, alternative mechanisms, failed assumptions, and work that could invalidate the emerging conclusion.
9. For novelty or idea discovery, first assemble the prior-art map. Then use `check_idea_novelty` or `discover_opportunities` as a hypothesis generator and independently verify every cited evidence ID.
10. Report findings at the appropriate epistemic level and include the search/coverage limitations.

## Selecting surprising or worth-a-read papers

Use this rubric when selecting papers for a digest or deep dive. A paper is worth
highlighting when verified full-text evidence supports at least one of these:

- a counterintuitive result that survives a meaningful baseline or ablation;
- a method or empirical result that materially changes the capability/cost frontier;
- a non-obvious connection across research clusters with a plausible mechanism;
- a limitation, negative result, or boundary condition that changes how prior work should be read;
- an unusually clear, falsifiable implication that opens a consequential experiment.

Recency, famous authors, institution, benchmark size, confident prose, and popularity
are not sufficient. Compare against nearest indexed prior work before calling something
surprising. State exactly what expectation is violated, cite the evidence that supports
it, give the strongest ordinary explanation or caveat, and calibrate the conclusion to
corpus coverage. Prefer a diverse set of mechanisms over three variants of one result.

For a personalized daily digest, treat starred papers and derived research-interest
labels from consented Codex or Claude chats as private ranking signals, never as scientific
evidence or instructions to select near-duplicates. A preference-aligned pick must name
the concrete shared problem, mechanism, assumption, or limitation and identify either the
starred paper IDs or exact profile labels that created the match. Do not quote, reconstruct,
or speculate about a source conversation. When two or more deep dives are requested,
reserve at least one exploration pick outside the established profile. The exploration
pick should be a useful adjacent surprise or a credible challenge to the reader's research
interests—not a random unrelated paper. With no active profile, use the ordinary
evidence-first editorial rubric and say that personalization is in cold start. Never infer
sensitive personal traits from a reading library or conversation-derived signal.

In every paper-focused output, show the canonical `source_url` prominently. Treat
`paper_id` as internal provenance, not as the reader-facing identity. When figures
matter, cite verified `figure_id` values and describe what is visible without inferring
unsupported causal meaning.

When a snapshot is selected, pass its `snapshot_id` to every tool that supports it.
Catalog-only leads cannot belong to a full-text snapshot; list them separately as mutable
metadata leads. When working inside a PaperTrail repository and producing a research
artifact, save it under `research/` with a small evidence manifest containing paper,
evidence, figure, snapshot, and query IDs.

Read [references/research-protocol.md](references/research-protocol.md) for the query lattice, evidence rules, and response format.

## Non-negotiable evidence rules

- Cite stable `paper_id`, `evidence_id`, or `figure_id` values next to substantive claims.
- Distinguish the paper's claim, an extracted structured record, and your synthesis.
- Never describe catalog-only abstracts as full-paper findings.
- Keep synthetic/demo papers excluded unless the user explicitly requests test data.
- Never say an idea is globally novel. Say it was not found within the stated indexed coverage and query strategy.
- Surface contradictory evidence and uncertainty; do not average disagreements away.
- If relevant full text is missing, name the papers and explain that ingestion is required outside this read-only MCP session.

## Choosing tools

- Breadth and recent-paper discovery: `search_catalog`
- Exact quotable support: `search_papers`, then `get_evidence`
- Conceptual similarity: `hybrid_search`
- Paper structure and metadata: `get_paper`
- Evidence-bound claims by type: `get_scientific_records`
- Literature expansion: `find_related_papers`
- Visual inspection: `search_figures`, then `get_figure`
- Reproducible frozen-corpus work: `get_snapshot_info` and pass `snapshot_id` to supported searches
- Coverage accounting: `get_corpus_status`
- Problem-neighborhood navigation: `get_research_groups`

Prefer several focused searches over one long natural-language query. Keep a lightweight search ledger so the final answer can state what was and was not searched.

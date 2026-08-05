# PaperTrail demo transcript

## Commands shown

```console
pip install papertrail-local
papertrail setup --interests-file interests.md --client codex
```

## 0:00–0:18 · The pain

Two hundred and five new AI papers arrived today. I do not need two hundred and five
summaries. I need to know which three deserve attention, what evidence they actually
contain, and whether they change an idea I am already exploring.

## 0:18–0:42 · Local onboarding

This is PaperTrail. Install the Python package, describe your interests in ordinary
Markdown, and connect Codex or Claude. The index, paper artifacts, preferences, and
research history stay in a local PaperTrail home. Ollama works fully locally, while the
OpenAI provider lets you bring compatible embedding and reasoning models.

## 0:42–0:58 · Daily run

One daily command discovered all two hundred and five papers, then ranked the surplus
before downloading expensive PDFs. Forty were enriched into two thousand seven hundred
and one evidence passages and three hundred and two figures. Sixty percent follows your
interests, twenty percent explores their frontier, and twenty percent preserves surprise.

## 0:58–1:42 · Dashboard

The dashboard explains that process. Edit the interest profile in plain language. See why
each paper ranked using relevance, recency, citation context, and the model's reading of
its problem and mechanism. Twenty-five problem neighborhoods consolidate papers targeting
the same question. Open the full group, save useful papers, or read a personalized deep
dive. Each essay links to its public source and exact local evidence. Figures come from the
acquired paper, and View paper opens the local PDF reader rather than the abstract page.

## 1:42–2:15 · Codex research

The index is also an MCP research surface. Here Codex searches the agent-memory thread,
opens exact passages, inspects figures, and challenges the pattern against nearby work.
The answer cites PaperTrail evidence IDs and public paper URLs, then proposes a falsifiable
test of whether versioned memory can distinguish genuine interface adaptation from
benchmark-specific recovery. This is not a claim of global novelty. It is an auditable
hypothesis bounded by the papers actually indexed.

## 2:15–2:30 · Handoff

PaperTrail is open source, local first, and deliberately small: Python, SQLite, your model
provider, and the research clients you already use. Install it, give it your interests,
and turn each day's paper surplus into a trail you can search, question, and build on.


## Genuine Codex research answer

## Bottom line

Agent memory appears capable of adapting tool use to changing runtime conditions, but this corpus does **not** establish that it can reliably detect and adapt to a tool interface changing over time.

The indexed work supplies most of the ingredients—time-aware experience filtering, memory revision, schema-conditioned calls, workflow transfer, and semantic failure detection—but no acquired paper directly tests an agent across an explicit API version boundary such as renamed arguments, changed types, response-shape changes, or tool splits/merges.

This novelty assessment is strictly bounded by the named PaperTrail corpus, not the global literature.

## Evidence landscape

| Capability | What the corpus establishes | Missing interface-drift test |
|---|---|---|
| Experience-driven adaptation | ExpG records the tool name/schema, context, input, response, success, cost, and time, then distills histories into reusable guidance. [ExpG](https://arxiv.org/abs/2608.03403v1), `ev_152393ca1d4021e9f47f` | Its summarizer is explicitly told to assume that the tool schema cannot change: `ev_62dad805654aa8b786e3`. |
| Temporal maintenance | ExpG sorts experiences by creation time and discards outdated or apparently harmful experiences using a sliding window. `ev_11f129f115e58956db6d` | Age or declining reward is not the same as detecting a schema-version transition or migrating old memories. |
| Revising stale memory | TARL distinguishes adding, revising an outdated belief, rejecting unreliable information, deferring verification, and ignoring redundancy; it evaluates temporal and sequential updates. [TARL](https://arxiv.org/abs/2608.03699v1), `ev_ed86c026e39f2f20fa4a` | Its evidence concerns factual memory transitions, not procedural memories tied to changing APIs. |
| Executable memory control | VerMem coordinates add, revise, soft-delete, retrieve, filter, summarize, and episodic-restore operations with local transition and global consistency verifiers. [VerMem](https://arxiv.org/abs/2608.03137v1), `ev_8cb6e94dab09c2224a47` | No interface-version representation, change detector, or API-evolution stream is demonstrated. |
| Transfer across tools | ToolLIFT abstracts tool-specific histories into function-level workflow graphs and reports OOD generalization to unseen tool sets. [ToolLIFT](https://arxiv.org/abs/2608.03468v1), `ev_8c6df041f216a00ddfd0` | Unseen tools test cross-tool abstraction, not within-tool temporal evolution or stale-memory retirement. |
| Parameter correctness | ParamBench conditions generation on a supplied target schema and shows that only 2.1% of audited failures violate schema format; most are schema-valid but value-wrong. [ParamBench](https://arxiv.org/abs/2608.03071v1), `ev_88ab3e80e8e0679df740` | The target schema is treated as current and given; the system is not asked to detect that an earlier schema became obsolete. |

## The strongest warning against a premature “yes”

ExpG’s failures are unusually informative. When runtime behavior conflicts with the declared schema, it can blame the agent instead of recognizing a tool/interface inconsistency. It can also overgeneralize a historically successful parameter convention and thereby cause later failures (`ev_8bf1ec44566823c4a95a`).

That is almost the phenomenon of interest, but it is presented as a failure case rather than a solved adaptation problem.

A nearby result reinforces the distinction between changing behavior and acquiring competence. In hybrid GUI–MCP agents, RL bonuses and injected tool documentation increased tool adoption substantially without improving accuracy; calls often executed successfully while being semantically ineffective. [Screenshots or Tools?](https://arxiv.org/abs/2608.03327v1), `ev_c9ea555eb4ec6030b24b`, `ev_a9da40f33ceff1559047`. Thus, faster reuse of revised memory would not by itself show successful adaptation—the evaluation must measure semantic task effects.

## Figure inspection

I visually inspected two ExpG page renders:

- `figure_035717c47913d7c597b0` shows the complete acquisition → filtering/summarization → reuse loop. Tool guidance may enter as a dynamic prompt or a stable schema constraint. The figure also visibly removes “outdated,” repeated, and bad experiences, but shows no schema-version identity, diff, or migration path.
- `figure_bbe728e7d84bc0bce49e` shows the incorrect-attribution and over-reaction cases. One example attributes failures to a bad device name even though behavior conflicts with the schema; another learns an overly rigid “always add Dr.” rule from a historically biased sample. These visually corroborate the exact passage in `ev_8bf1ec44566823c4a95a`.

## Falsifiable paper idea

**Versioned Interface Memory for Agents**

Hypothesis: attaching procedural memories to canonical schema fingerprints and validity intervals, then applying change-point detection and transactional revision/quarantine, reduces post-interface-change semantic failure and cumulative adaptation regret relative to ordinary experience memory.

Experiment:

1. Let agents learn tools under interface version \(v_1\).
2. At hidden change points, apply controlled mutations:

   - argument rename, removal, or type change;
   - new required or conditional fields;
   - response-field relocation or type change;
   - tool split, merge, or capability reassignment;
   - silent semantic drift with unchanged schema.

3. Compare:

   - no memory;
   - static procedural memory;
   - latest-documentation-only retrieval;
   - ExpG-style experience guidance;
   - versioned memory with schema fingerprints, accepted/pending/quarantined ledgers, and migration operators.

4. Measure change-detection delay, false alarms on stationary tools, post-change semantic success, adaptation regret, stale-memory invocation rate, recovery cost, and backward transfer when an older interface returns.

The hypothesis is falsified if versioned memory fails to beat latest-documentation-only retrieval on post-change regret or semantic success, or if its false-alarm/quarantine cost erases its recovery advantage. A further mechanistic falsifier is that schema fingerprints help structural mutations but not silent semantic drift; that would show the gains come from explicit version metadata rather than memory reasoning.

Nearest indexed overlaps are ExpG’s evolving procedural guidance, TARL’s temporal transactions, VerMem’s revision operations, ToolLIFT’s transferable workflows, and ParamBench’s schema-grounded parameter generation. I found no indexed acquired paper combining those elements in a controlled temporal interface-evolution benchmark. That is a corpus-bounded gap, **not a claim of global novelty**.

## Coverage and warnings

Research used snapshot `cs-AI-2026-08-04`:

- Coverage: 103 primary `cs.AI` preprints dated 2025-08-05 through 2026-08-04.
- Acquired full text: 40 papers.
- Evidence: 2,701 passages, 1,332 scientific records, and 302 visual-evidence items.
- Source-class limitation: the snapshot contains preprints only; no peer-reviewed papers or reviews.
- Query families covered interface/schema drift, API evolution and deprecation, nonstationary tool use, procedural and temporal memory, parameter errors, unseen-tool transfer, silent tool failures, semantic-success gaps, and counterevidence.
- Explicit interface-evolution queries returned adjacent work rather than a direct evaluation. Empty direct retrieval is not evidence of global absence.
- `get_evidence` does not accept a snapshot parameter and reported the mutable working set; all cited IDs were first located in snapshot-scoped searches and then retrieved exactly.
- PaperTrail’s novelty-check service timed out twice. The bounded assessment above therefore rests on the independently inspected candidate map and exact evidence, not the automated novelty synthesis.
# PaperTrail local-first architecture

PaperTrail keeps the PDF design's epistemic and provenance boundaries while collapsing its operational footprint into one Python process, one SQLite file, one artifact directory, and an optional local Ollama runtime.

```text
arXiv / local PDFs
        │
        ▼
immutable artifacts ──► sections ──► evidence passages
                                      │          │
                            SQLite FTS5          ├──► local embeddings
                                      │          │
                                      └────┬─────┘
                                           ▼
                                  reciprocal-rank fusion
                                           │
              ┌────────────────────────────┼───────────────────────────┐
              ▼                            ▼                           ▼
      exact evidence tools       scientific extraction       snapshot-pinned search
                                           │
                       ┌───────────────────┴───────────────────┐
                       ▼                                       ▼
               novelty challenge                    opportunity generation
                       └───────────────────┬───────────────────┘
                                           ▼
                         evidence manifest + warnings + model versions
                                           │
                                           ▼
                                    read-only agent MCP
                                           │
                              daily Codex/Claude analyst
                                           │
                         validated trends + Markdown deep dives
                                           │
                          local dashboard + PDF/figure reader

consented local Codex / Claude histories
        │ read-only adapters; user turns only
        ▼
redaction ──► structured research-interest events ──► versioned preference profile
                                                           │
favourites ────────────────────────────────────────────────┘
                                                           │
                              daily enrichment lane allocation (60/20/20)
```

## Durable contracts

### Evidence

Every passage carries a paper ID, immutable paper-version ID, section, page range when available, source URL, content hash, and stable evidence ID.

### Structured scientific records

Contributions, methods, assumptions, empirical results, limitations, and future work are Level 2 machine extractions. Each accepted record must cite valid evidence passages from its own paper version. Numeric values must occur verbatim in that evidence.

### Retrieval

FTS5 and dense candidates are independently ranked, then combined with reciprocal-rank fusion using `k=60`. The response exposes match reasons, embedding model, snapshot, exact passages, and known gaps. Retrieval finishes before synthesis.

### Novelty challenge

An idea is compared along mechanism, assumptions, data, evaluation setting, and failure mode. The output includes overlap and differentiating dimensions, unresolved limitations, potential counterevidence, and falsifying experiments. It is always corpus-bounded system synthesis.

### Opportunity discovery

Candidate generation uses recurring limitations, future work, incompatible assumptions, and empirical records. Every candidate needs evidence, a concrete mechanism, a risk, and a falsifying experiment. PaperTrail then sends the hypothesis through the novelty-challenge pipeline before returning it.

### Research artifacts

Novelty and discovery runs are persisted with input, snapshot, exact evidence IDs, model versions, prompt version, and timestamp. This makes an analysis replayable and auditable.

### Daily intelligence

The daily scheduler overlaps discovery windows and retains metadata plus abstracts for
the complete configured surplus. A configurable budget, 40 papers by default, determines
which pending papers receive PDF acquisition and full enrichment. Once a preference
profile is sufficiently reliable, the default allocation is 60% aligned work, 20%
frontier work, and 20% deliberate exploration. Scores, lane, explanation, and profile
version are stored for every discovery record considered.

After publishing an immutable rolling snapshot, a bounded Codex or Claude process
receives at most 40 newly acquired candidates and can use only the read-only research
surface. Candidate inputs, client/model, status, trends, and failures are stored in
`daily_digest_runs`.

Blogs enter `daily_blogs` only after their selected paper, canonical source URL, exact
evidence IDs, figure IDs, related paper IDs, and length have been validated locally.
The dashboard reads those tables directly. Paper PDFs and figures are served by stable
IDs from the artifact store; arbitrary filesystem paths are never accepted.

### Adaptive research profile

Chat learning is off until setup records source-specific consent. Read-only adapters
discover local Codex and Claude JSON/JSONL sessions, extract user-authored research turns,
redact credential-shaped content, and pass bounded text to the configured reasoning
provider. With a remote provider, that redacted input leaves the machine; setup and the
README make this explicit.

PaperTrail persists opaque session fingerprints, content digests, normalized research
events, and aggregate profile versions. It never persists raw conversation turns or
assistant answers. Unchanged digests cause no model call, while changed sessions replace
their earlier derived events transactionally. Disabling stops future reads but retains
derived signals; forgetting removes consent, hashes, and events for that source.

Preference signals rank candidates but cannot support scientific claims. Favourites have
the highest authority, explicit chat interests outrank inferred ones, and chat signals
decay over time. Personalized ingestion activates after three favourites or eight
high-confidence events spanning at least three sessions with an explicit interest.

### Organization and consolidation

Each dated snapshot can produce an immutable-input organization run. Paper vectors are
centroids of their evidence-passage embeddings, projected to a compact deterministic
space for efficient personal-scale clustering. Lexical problem features come from the
title, abstract, and evidence-bound contribution, method, assumption, and limitation
records. Online centroid assignment combines 78% semantic similarity with 22% lexical
overlap when vectors exist, consolidates sufficiently close singleton groups, and
persists membership scores and new-paper flags.

Cluster labels are deterministic TF-IDF-style terms. Both labels and membership are
explicitly navigation metadata rather than source evidence. Codex and Claude can inspect
the latest map through `get_research_groups`, then verify any inferred pattern against
exact passages.

## Failure behavior

- Missing local models fail explicitly; lexical search remains available but intelligent commands do not fabricate a fallback.
- Invalid evidence IDs reject extracted records.
- Unverified numeric values reject extracted records.
- Empty retrieval returns an explicit warning.
- A snapshot pins exact paper versions and cannot be republished with different contents.
- Failed daily analysis does not roll back ingestion and retains its candidate set for retry.
- A malformed or unavailable history source does not stop paper discovery or ingestion.
- Preference cursors advance only after validated extraction succeeds.
- A blog citing evidence or figures owned by another paper is rejected atomically.
- Generated opportunities remain labeled system synthesis and never become source evidence.

## Scaling path

The current dense scan is suitable for a personal corpus and keeps installation easy. When measured latency requires it, retain all contracts and replace only the vector-scoring adapter with SQLite Vec1/sqlite-vec. PostgreSQL or OpenSearch should be introduced only when concurrency or corpus size justifies an external service.

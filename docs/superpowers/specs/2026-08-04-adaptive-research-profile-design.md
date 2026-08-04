# Adaptive Research Profile and Personalized Ingestion

Date: 2026-08-04
Status: Approved design

## Purpose

PaperTrail should learn a user's research interests from both explicit library choices and
their local Codex or Claude research conversations. It should use those interests to spend
expensive ingestion and enrichment work on the most useful papers without shrinking the
underlying corpus or trapping the user in an echo chamber.

The feature is local-first and opt-in. Setup asks once for permission to read supported local
chat histories. After consent, every scheduled daily run automatically processes only new or
changed conversations. PaperTrail stores derived research-interest signals and incremental
source cursors, not copies of raw conversations.

## Product principles

1. **Explicit actions outrank inferred interests.** A favourite is a stronger signal than a
   topic merely discussed in a chat.
2. **Discovery remains broad.** PaperTrail continues discovering and retaining metadata and
   abstracts for every paper in the configured source categories and date window.
3. **Personalization allocates expensive work.** It affects PDF acquisition, parsing, figure
   extraction, embeddings, scientific extraction, and deep-dive selection—not whether a paper
   is visible at all.
4. **Exploration is guaranteed.** Trending, novel, and deliberately adjacent papers retain a
   fixed share of the enrichment budget.
5. **Preferences are transparent and reversible.** Users can inspect, disable, forget, and
   rebuild the local profile.
6. **Conversation text is not evidence.** Chat-derived signals may rank papers but can never
   substantiate a scientific claim.
7. **No sensitive profiling.** Extract only research topics, problems, methods, artifacts,
   and explicit positive or negative research preferences. Do not infer personal, medical,
   political, demographic, or other sensitive traits.

## User experience

### Setup

`papertrail setup` asks whether PaperTrail may automatically learn from local Codex and Claude
history. Consent is stored independently per source. Non-interactive installations can use
explicit flags:

```text
--learn-from codex
--learn-from claude
--no-chat-learning
```

The default is no chat access unless consent has already been recorded or an affirmative flag
is supplied. Existing installations therefore do not silently begin reading histories after an
upgrade.

When enabled, setup discovers supported local history locations and reports which sources were
found. A missing source is a non-fatal condition; daily runs continue and retry discovery later.

### Automatic daily behavior

Before arXiv discovery and enrichment, `papertrail daily`:

1. scans each consented source for new or changed sessions;
2. extracts research-preference events from relevant user-authored turns;
3. updates the aggregated profile with time decay and source weighting;
4. discovers the complete configured paper surplus and stores metadata plus abstracts;
5. scores discovered papers against the profile, trends, novelty, and exploration policy;
6. spends the full-enrichment budget according to the resulting allocation;
7. organizes the new snapshot and generates personalized daily deep dives.

Routine runs require no extra command or manual chat export.

### Preference controls

PaperTrail adds these commands:

```text
papertrail preferences inspect
papertrail preferences sources
papertrail preferences enable codex|claude
papertrail preferences disable codex|claude
papertrail preferences forget codex|claude|all
papertrail preferences rebuild
```

`inspect` shows weighted topics, methods, problems, exclusions, their provenance class, and the
last update time. It does not display raw chat turns. `disable` stops future reading but retains
existing derived signals; `forget` removes the source's signals, cursors, and fingerprints.
`rebuild` deletes derived chat signals and regenerates them from currently consented sources.

## Architecture

### History adapters

A small adapter interface isolates client-specific history layouts:

```text
HistoryAdapter
  discover() -> iterable[SessionRef]
  changed_since(cursor) -> iterable[SessionRef]
  read_user_turns(session) -> iterable[ResearchTurn]
```

The first release includes `CodexHistoryAdapter` and `ClaudeHistoryAdapter`. Adapters use known
local defaults and optional configured paths. They tolerate versioned directory layouts and
skip unreadable or malformed sessions with a recorded warning. They must never invoke Codex or
Claude, upload history, or modify client-owned files.

Each session receives a stable source fingerprint derived from source type, canonical path,
session identifier, modification metadata, and content digest. Cursors make normal daily scans
incremental. A changed digest causes only that session's prior signals to be replaced.

### Preference extraction

The extractor filters for research-bearing user turns before calling the configured reasoning
provider. Greetings, code-only operational chatter, secrets, credentials, and unrelated personal
conversation are excluded. Input is bounded and processed in small session chunks.

The structured extractor returns zero or more events:

```text
PreferenceEvent
  kind: topic | problem | method | artifact | positive | negative
  label: normalized concise concept
  context: non-sensitive research context
  confidence: 0..1
  explicitness: explicit | inferred
  source: favorite | codex | claude | interaction
  observed_at
```

Events must describe research intent rather than scientific truth. Extraction output is schema
validated. Raw prompts and model responses are held only in process memory and are not logged.

### Persistence

New SQLite tables keep consent, incremental state, and derived signals separate:

- `preference_sources`: source, enabled, consented_at, optional history path, last scan, status,
  and error summary.
- `preference_sessions`: source, opaque session fingerprint, content digest, last processed time,
  and event count. No title or transcript body is stored.
- `preference_events`: normalized signal fields, confidence, explicitness, source, opaque session
  fingerprint, observed time, and expiry state.
- `paper_priority_scores`: discovery record, profile version, component scores, final lane,
  explanation, and timestamp.
- `preference_profile_versions`: a versioned summary of the weights and policy used for an
  ingestion or digest run.

Foreign keys allow source-specific forgetting to remove all associated sessions and events.
Preference tables remain local in the existing private PaperTrail home and follow its file
permissions.

### Profile aggregation

The profile combines signals in descending authority:

1. favourites and explicit dislikes;
2. explicit statements in chats;
3. repeated inferred chat interests;
4. opened or selected deep dives.

Recent signals receive exponential time decay, while favourites do not decay until unstarred.
Repeated mentions increase confidence with a cap so a single recurring workflow cannot dominate
the profile. Negative signals reduce preference affinity but never remove a paper from discovery,
search, or the exploration lane.

Personalized ingestion activates only after the profile is reliable: either three favourites, or
eight high-confidence events drawn from at least three distinct sessions and containing at least
one explicit interest. Until then, PaperTrail uses broad editorial, frontier, and exploration
ranking. Personalized blog selection may still use a smaller favourite profile because its cost
and recall consequences are limited to the daily reading list.

The aggregate contains human-readable weighted concepts and, when embeddings are available, a
semantic centroid or small set of interest centroids. Every daily run snapshots a profile version
so its prioritization remains explainable and reproducible.

## Personalized ingestion policy

### Broad discovery layer

All papers matching the configured category and date range are discovered. Title, authors,
categories, source URLs, publication metadata, and abstracts are indexed for every result. This
is the searchable safety net and is never filtered by preference.

### Priority scoring

Each discovery record receives component scores:

- semantic and lexical affinity to the preference profile;
- recency and cross-paper trend strength;
- novelty or distance from already indexed work;
- editorial quality proxies available from metadata;
- deterministic exploration sampling.

Scores are explanations, not evidence. The stored explanation names only research concepts, for
example, “matches tool-use evaluation and agent adaptation,” rather than exposing chat text.

### Enrichment lanes

When the daily candidate count exceeds the configured full-enrichment budget, the default
allocation is:

- **60% preference:** highest profile affinity;
- **20% frontier:** trending or unusually novel work, independent of profile affinity;
- **20% exploration:** diverse papers outside the strongest profile neighborhoods.

Small budgets use deterministic rounding while preserving at least one frontier or exploration
paper whenever two or more papers can be enriched. Duplicate candidates across lanes are filled
from the next eligible candidate. With no active profile, the preference share is redistributed
to frontier and broad editorial ranking.

Scheduled setup exposes `--daily-enrichment-budget`, defaulting to 40 papers. A value of `0`
means full enrichment for every daily discovery and therefore disables priority-based cost
savings while retaining personalized deep-dive selection. The run record stores both the budget
and lane counts.

Selected papers receive the current full pipeline: PDF acquisition, text and section parsing,
figure capture, passage indexing, embeddings, and evidence-bound scientific extraction.
Unselected discoveries retain metadata and abstracts and can be promoted later when they:

- match a changed profile;
- are starred or opened;
- are requested through search or deep research;
- become part of a trend or cluster;
- are explicitly acquired by the user.

The first implementation applies this budgeting to scheduled daily ingestion. Explicit commands
such as `add-arxiv`, `add-pdf`, and `arxiv ingest --all` keep their existing user-directed meaning.

## Deep-dive selection integration

The existing favourite-based personalization profile becomes a unified research profile.
Favourites remain directly attributable by paper ID, while chat signals are cited only as named
interest concepts. Daily blog output continues enforcing both a preference-aligned selection and
an exploration selection when multiple blogs are requested.

The dashboard explains each selection using non-sensitive language and one of these labels:

- `For you`: matched favourites or durable research interests;
- `Frontier`: selected because it is trending or unusually novel;
- `Explore`: selected to broaden the user's research neighborhood;
- `Editor pick`: used before a profile becomes active.

## Privacy, safety, and failure handling

- Chat learning is disabled until explicit consent is recorded.
- History access is read-only and local.
- Raw transcript bodies, prompts, and assistant answers are not persisted by PaperTrail.
- Tokens, credential-shaped strings, and common secret fields are removed before extraction.
- PaperTrail extracts only user-authored research intent; assistant suggestions alone do not
  become user preferences.
- Source errors are isolated. One malformed history file cannot fail daily paper ingestion.
- Provider failure leaves the previous profile active and records the scan as incomplete; cursors
  advance only after validated extraction succeeds.
- A daily run can be repeated idempotently. Session digests prevent duplicate events and profile
  versions make priority decisions auditable.
- Forgetting a source immediately removes its events and rebuilds the aggregate profile.

## Configuration

The daily profile gains:

```json
{
  "preferences": {
    "chat_learning": true,
    "sources": {
      "codex": {"enabled": true},
      "claude": {"enabled": true}
    },
    "ingestion": {
      "personalized": true,
      "daily_enrichment_budget": 40,
      "preference_share": 0.6,
      "frontier_share": 0.2,
      "exploration_share": 0.2
    }
  }
}
```

Users may disable personalized ingestion while keeping personalized blogs, or disable chat
learning while retaining favourite-based personalization. Provider credentials and raw history
remain outside the profile file.

## Testing and acceptance criteria

Unit tests cover adapters, filtering, redaction, schema validation, session replacement, source
forgetting, time decay, authority weights, and deterministic lane allocation. Fixtures contain
synthetic history only.

Integration tests prove that:

1. setup records source-specific consent without copying chat text;
2. the first daily run processes existing sessions and a second unchanged run does no extraction;
3. adding a new session updates the profile automatically;
4. a changed session replaces rather than duplicates its signals;
5. every discovered paper retains searchable metadata and abstract content;
6. full enrichment follows the 60/20/20 policy when a budget is active;
7. frontier and exploration papers survive even under a strong preference profile;
8. explicit manual ingestion remains unaffected;
9. disabling and forgetting a source behave differently and correctly;
10. logs, database rows, API responses, and dashboard explanations contain no raw conversation
    body or credential-shaped fixture value.

The feature is complete when a user can opt in once, continue using Codex or Claude normally,
and see future daily enrichment and deep dives adapt automatically while PaperTrail continues to
retain the full discoverable paper surplus.

## Deliberately deferred

- Cloud-synced conversation histories.
- Organization-wide or multi-user profiles.
- Behavioral tracking beyond explicit PaperTrail interactions.
- Fine-grained UI editing of individual inferred events; the first release supports inspection,
  source-level forgetting, and rebuilding.
- Personalized source-category expansion. Categories remain an explicit user configuration.

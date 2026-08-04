# Adaptive Research Profile Implementation Plan

## Goal

Add automatic, consented learning from local Codex and Claude histories and use the resulting
research profile to prioritize scheduled paper enrichment while preserving broad discovery.

## Stage 1: Preference data model and history adapters

Files:

- `src/papertrail/db.py`
- `src/papertrail/preferences.py` (new)
- `tests/test_preferences.py` (new)

Tasks:

1. Add source, session, event, profile-version, and paper-priority tables.
2. Implement read-only Codex and Claude JSON/JSONL adapters with configured-path overrides.
3. Extract only user-authored text from supported event shapes.
4. Add secret redaction, research-turn filtering, stable session fingerprints, and digest-based
   incremental processing.
5. Add source enable/disable/forget/rebuild/inspect operations.
6. Test with synthetic histories and assert raw text and secrets never enter SQLite.

## Stage 2: Structured preference extraction and aggregation

Files:

- `src/papertrail/preferences.py`
- `src/papertrail/providers.py`
- `tests/test_preferences.py`

Tasks:

1. Define a strict structured-output schema for research preference events.
2. Send bounded, redacted user-turn batches to the configured reasoning provider.
3. Validate allowed kinds, normalized labels, confidence, explicitness, and non-sensitive context.
4. Replace a changed session's events transactionally and advance its digest only on success.
5. Aggregate favourite and conversation signals with authority weights, repetition caps, and time
   decay; expose activation confidence and compact prompt context.

## Stage 3: Personalized enrichment selection

Files:

- `src/papertrail/preferences.py`
- `src/papertrail/arxiv_batch.py`
- `src/papertrail/cli.py`
- `tests/test_preferences.py`

Tasks:

1. Score every discovered title and abstract using lexical affinity and embeddings when available.
2. Allocate deterministic preference, frontier, and exploration lanes within the daily budget.
3. Persist component scores, lane, explanation, and profile version.
4. Extend group acquisition to accept an explicit ordered set of discovery IDs without changing
   manual `--all` or `--limit` behavior.
5. Treat a deliberately budgeted acquisition as complete for the scheduled pipeline while leaving
   unselected discovery records available for future promotion.

## Stage 4: Daily, CLI, and deep-dive integration

Files:

- `src/papertrail/cli.py`
- `src/papertrail/daily_digest.py`
- `src/papertrail/profile.py`
- `README.md`
- `src/papertrail/skill/papertrail-deep-research/SKILL.md`
- `tests/test_public.py`

Tasks:

1. Add setup consent flags and `--daily-enrichment-budget`.
2. Add the `preferences` command family.
3. Run automatic history sync before daily discovery.
4. Feed unified profile concepts into deep-dive ranking while retaining favourite paper IDs for
   direct attribution and enforcing exploration.
5. Document privacy, automatic operation, inspection, forgetting, rebuilding, and budget controls.

## Stage 5: Verification and release

1. Run all unit and integration tests.
2. Run Ruff, syntax compilation, and `git diff --check`.
3. Build the wheel and inspect its contents for the new module and updated skill.
4. Exercise the CLI against synthetic histories in a disposable home.
5. Bump the package minor version, commit the implementation, push `main`, and verify the remote
   commit.

## Completion checks

- Setup never enables history reading without an affirmative option or interactive consent.
- An unchanged second scan performs zero model extractions.
- Changed sessions replace signals rather than duplicating them.
- Only research preference events, hashes, cursors, and summaries persist.
- All discovered papers retain metadata and abstracts.
- The daily budget includes preference, frontier, and exploration lanes.
- Manual ingestion semantics are unchanged.
- Existing profiles continue working with chat learning disabled by default.

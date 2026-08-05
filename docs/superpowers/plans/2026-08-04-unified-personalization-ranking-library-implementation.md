# Unified Personalization, Ranking, and Library Implementation Plan

Date: 2026-08-04
Design: `docs/superpowers/specs/2026-08-04-unified-personalization-ranking-library-design.md`

## Stage 1: Persist explicit interests and citation metadata

Files:

- `src/papertrail/db.py`
- `tests/test_preferences.py`
- `tests/test_public.py`

Work:

1. Add an idempotent singleton table for the exact dashboard-authored interest note, extraction
   status, error summary, and timestamps.
2. Add citation metadata keyed by paper and provider, including provider work ID, counts, match
   provenance, confidence, and refresh timestamp.
3. Extend migration tests so an existing 0.10 database gains both tables without losing profile,
   favourite, organization, or blog data.

Gate: database initialization and migration tests pass.

## Stage 2: Integrate explicit text with the unified preference profile

Files:

- `src/papertrail/preferences.py`
- `src/papertrail/daily_digest.py`
- `tests/test_preferences.py`

Work:

1. Add read and replace operations for the explicit note.
2. Extract schema-validated events using the configured reasoning provider and persist them under
   a dedicated high-authority source.
3. Provide deterministic lexical fallback when extraction or embedding fails and mark the note
   pending for the next daily retry.
4. Update aggregation weights and activation logic so one non-empty explicit dashboard note can
   activate personalized ingestion without weakening existing consent boundaries for chat data.
5. Ensure editing replaces old explicit events atomically; clearing affects only the explicit
   source.

Gate: tests cover create, edit, clear, restart, authority, fallback, and retry behavior.

## Stage 3: Acquire and cache scholarly citation signals

Files:

- `src/papertrail/citations.py` (new)
- `src/papertrail/arxiv_batch.py`
- `src/papertrail/daily_digest.py`
- `src/papertrail/profile.py`
- `src/papertrail/config.py`
- `tests/test_citations.py` (new)

Work:

1. Implement a standard-library Semantic Scholar client using batched paper-ID lookup, timeouts,
   bounded retries, an optional `SEMANTIC_SCHOLAR_API_KEY`, and a descriptive user agent.
2. Resolve arXiv IDs first, DOI second, and reject unsafe approximate matches.
3. Upsert only validated fields while preserving older cached data on provider failure.
4. Enrich newly discovered papers and incrementally refresh records older than seven days during
   the daily workflow.
5. Return warnings and counts in run output without turning metadata failure into ingestion
   failure.

Gate: mocked tests cover batching, identifier normalization, caching, stale refresh, malformed
responses, throttling, and non-blocking failure.

## Stage 4: Rank every member inside its existing research group

Files:

- `src/papertrail/ranking.py` (new)
- `src/papertrail/organization.py`
- `src/papertrail/service.py`
- `tests/test_ranking.py` (new)
- `tests/test_organization.py`

Work:

1. Implement normalized neighborhood relevance, unified-profile affinity, recency, and
   age-normalized citation components.
2. Apply 30/30/25/15 personalized weights and 50/0/35/15 unpersonalized weights, redistributing
   unavailable components proportionally.
3. Add deterministic tie-breaking and concise reason generation.
4. Re-rank only within existing cluster membership when `latest_organization` is read. Preserve
   every member and expose formula version, component scores, citation freshness, and reasons.
5. Reuse existing embeddings where compatible and fall back to lexical affinity without network
   calls during a page request.

Gate: deterministic unit tests prove weight redistribution, new-paper neutrality, age cohorts,
profile-sensitive reordering, unchanged membership, and fallback to stored cluster order.

## Stage 5: Expose profile editing and an unambiguous local reader API

Files:

- `src/papertrail/api.py`
- `src/papertrail/service.py`
- `src/papertrail/daily_digest.py`
- `tests/test_public.py`

Work:

1. Add a read endpoint for explicit/implicit profile summaries and a same-origin JSON endpoint to
   replace the explicit note.
2. Enforce content type and a bounded note size; return extraction status, profile version, and
   refreshed group ranking information.
3. Extend favourites and paper details with citation/ranking data and explicit artifact
   availability/type.
4. Keep the existing range-capable local artifact endpoint and canonical source URL distinct.

Gate: API tests cover valid edit, clear, oversized input, cross-origin rejection, provider
fallback, paper detail, and missing artifact.

## Stage 6: Build the dashboard interactions

Files:

- `src/papertrail/web/app.js`
- `src/papertrail/web/styles.css`
- `src/papertrail/web/index.html`

Work:

1. Preserve the already-implemented per-group `View all N papers` / `Show less` behavior.
2. Add the editable research-interest card with saved, saving, pending, error, and understood
   states. Re-fetch the profile and organization after save for immediate ranking.
3. Render ranked group members with compact recommendation reasons and citation metadata where
   available.
4. Replace favourite cards with accessible compact expandable rows.
5. Add `#/paper/{paper_id}` and route all paper titles and `View paper` actions to it. Embed local
   PDFs, render text artifacts safely, and provide a missing-artifact state.
6. Keep `Open source` as the only action that opens the canonical external page.
7. Preserve responsive behavior, keyboard focus, expansion state, and favourite actions.

Gate: JavaScript syntax check plus browser verification on compact and desktop viewports.

## Stage 7: Daily workflow, packaging, and documentation

Files:

- `src/papertrail/cli.py`
- `src/papertrail/scheduler.py`
- `src/papertrail/__init__.py`
- `pyproject.toml`
- `README.md`
- `docs/ARCHITECTURE.md`

Work:

1. Include pending explicit-profile extraction and citation refresh in the one-command daily
   workflow.
2. Document automatic citation enrichment, the optional Semantic Scholar key, ranking semantics,
   the explicit-interest card, compact library, and local reader.
3. Update architecture and package version while retaining dependency-free default installation.

Gate: README commands match CLI help and package metadata.

## Stage 8: End-to-end verification and release handoff

1. Run Ruff, the complete test suite, JavaScript syntax validation, and package build.
2. Launch the public code against a disposable test home, validate profile editing and ranking,
   and confirm citation failure cannot stop ingestion.
3. Launch against the populated local corpus at port 8877, validate group expansion, compact
   favourites, local PDF reading, and canonical arXiv source navigation.
4. Capture updated dashboard, research-group, favourites, and reader screenshots for README
   examples where they improve onboarding.
5. Review the final diff for private-provider names, credentials, local absolute paths, and
   accidental generated data before committing and pushing.


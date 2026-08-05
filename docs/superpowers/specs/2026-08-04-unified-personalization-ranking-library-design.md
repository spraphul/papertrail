# Unified Personalization, Ranking, and Library Experience

Date: 2026-08-04
Status: Approved direction; awaiting written-spec review

## Problem

PaperTrail already derives research interests from favourites and consented local Codex or
Claude history, but users cannot directly state or edit their interests in the dashboard. The
same profile should govern what is enriched, how papers inside a problem neighborhood are
ranked, and which papers receive daily deep dives.

Research-group membership is useful, but its current member order mainly reflects whether a
paper is new and its similarity to the cluster. It does not combine personal relevance,
recency, or scholarly impact. Citation counts are not currently acquired.

The favourites page renders every saved paper as a large card, which becomes unwieldy as the
library grows. Paper actions are also unclear: `Read paper` directly opens a local artifact in a
new browser tab while `Open source` opens the canonical source page. For arXiv papers those two
destinations can look like duplicate external actions rather than a local reader and a source
link.

## Design principles

1. **One profile, many consumers.** Explicit interests, favourites, and consented inferred
   signals produce one versioned profile used by ingestion, group ranking, and deep dives.
2. **Explicit intent has the highest authority.** Text deliberately entered in PaperTrail
   outweighs favourites and inferred chat signals.
3. **Ranking is explainable and deterministic.** An LLM structures free text and helps organize
   papers, but it does not reorder hundreds of papers on every page load.
4. **Impact is contextual, not a popularity contest.** Citation signals are age-normalized and
   remain a minority component.
5. **Missing external metadata never blocks local research.** Citation lookup degrades cleanly,
   cached values remain usable, and missing values are not treated as zero-impact evidence.
6. **Reading and provenance are distinct.** `View paper` opens PaperTrail's local reader;
   `Open source` opens the canonical publisher or arXiv page.

## Unified research-interest card

The main dashboard gains a `Your research interests` card near the top of the page. It contains:

- a free-text textarea containing the user's current explicit interest statement;
- concise guidance that natural language is accepted, including positive interests, priorities,
  and topics the user wants less of;
- `Save interests` and `Cancel` actions;
- a saved/version status and a short summary of how the text was understood;
- separate compact chips for implicit signals, labelled as learned from favourites or consented
  local assistant history.

Example text:

```text
I care about agents that adapt to changing tools, reliable memory, and evaluation under
distribution shift. Prefer mechanism-heavy work with strong experiments. De-emphasize pure
prompt-engineering benchmarks.
```

The exact text is stored locally because the user intentionally authored it as editable profile
data. On save, PaperTrail creates or replaces the explicit-profile source and rebuilds the
aggregate preference profile. It never silently rewrites the textarea into model-generated
prose.

The configured reasoning provider extracts normalized positive interests, negative interests,
research styles, problems, mechanisms, and evaluation preferences. Extraction is schema
validated and the derived events retain provenance as `dashboard_explicit`. The exact note has
greater authority than all inferred sources. Clearing and saving the textarea removes these
explicit events without deleting favourites or chat-derived signals.

Saving immediately causes the API response for current research groups to use the new profile,
so a dashboard refresh or route render shows newly ranked members without requiring a new
organization or daily run. Future discovery prioritization, full-text enrichment, and deep-dive
selection use the same profile version.

If the reasoning provider is unavailable, PaperTrail still saves the exact text, derives a
temporary lexical representation, and immediately re-ranks with that representation. It marks
structured extraction as pending and retries it on the next daily run. The last valid structured
profile remains available until the retry succeeds.

## Automatic citation enrichment

PaperTrail uses the Semantic Scholar Academic Graph API as the default citation metadata
provider. It supports scholarly-paper identifiers and citation fields, offers batch operations,
and permits public unauthenticated access with shared throttling. Users may optionally set a
Semantic Scholar API key for more reliable limits, but a key is not required for setup.

For arXiv records, PaperTrail resolves the work using its arXiv identifier. DOI is used when
available, followed by a guarded title-and-author match only when identifier matching is not
possible. Approximate matches must meet a conservative confidence threshold; otherwise the paper
remains unmatched.

The local citation record stores:

- PaperTrail paper ID and provider name;
- provider work ID and match method;
- total citation count and influential citation count when supplied;
- reference count when supplied;
- match confidence and retrieval timestamp.

Raw provider responses are not required for ranking and are not retained. New papers are looked
up during ingestion in batches. Existing stale records are refreshed incrementally by the daily
run, with a default freshness window of seven days. HTTP throttling, timeouts, malformed records,
or an unavailable service are recorded as enrichment warnings and never fail paper ingestion.

Citation data is descriptive metadata, not scientific evidence. The UI identifies Semantic
Scholar as its source and shows when the count was last refreshed.

## Ranking papers inside research groups

Clustering and ranking remain separate operations. LLM-assisted organization determines whether
a paper belongs in a problem neighborhood. The ranker only orders accepted members inside that
group; it cannot move a paper between groups.

The default personalized score is:

| Signal | Weight | Meaning |
| --- | ---: | --- |
| Neighborhood relevance | 30% | Existing semantic/lexical membership similarity |
| Personal affinity | 30% | Semantic and lexical match to the unified profile |
| Recency | 25% | Smooth decay based on publication date |
| Citation impact | 15% | Age-normalized citation strength |

When no active profile exists, the personal-affinity weight is redistributed to neighborhood
relevance and recency, producing 50% relevance, 35% recency, and 15% citation impact. When a
component is unavailable for a paper, its weight is redistributed proportionally across the
available components rather than assigning a misleading zero.

Citation impact uses `log1p(citation_count)` and a percentile among papers of similar publication
age. Very recent papers receive a neutral citation component until enough time has passed for
citations to be meaningful. Influential citations may break close ties but do not add a second
popularity weight. This prevents old survey papers from permanently dominating new work.

Ties are resolved by publication date, cluster similarity, title, and paper ID, in that order, so
the result is reproducible. The organization API returns each component, the final score, and up
to two concise reasons such as `Strong match for adaptive tool use`, `New this week`, or `Highly
cited for its age`. These are recommendation explanations, not evidence claims.

The Research Groups page initially shows eight ranked papers. `View all N papers` navigates to a
dedicated `#/groups/{cluster_id}` page that renders the complete neighborhood in ranked order,
without changing any other group card. Back navigation returns to the compact group overview.
The dashboard overview continues to show three ranked members.

## Compact favourites library

The favourites route becomes a compact list instead of a grid of full paper cards. Each collapsed
row shows:

- title;
- first authors, publication date, and saved date;
- star/remove control;
- one or two ranking-reason chips when available;
- an accessible expand chevron.

Expanding a row reveals the full abstract, all authors, citation metadata, ranking components,
`View paper`, and `Open source`. Several rows may be expanded simultaneously. Expansion state is
kept for the current browser session and survives a star-triggered re-render where the paper
still exists. Removing a favourite removes its row immediately.

## Local paper reader and source actions

Paper titles and `View paper` actions navigate to an internal route,
`#/paper/{paper_id}`. The route fetches the existing paper-detail endpoint and displays title,
authors, abstract, publication metadata, sections, extracted figures, favourite state, and an
embedded same-origin local artifact.

PDF artifacts use the existing range-capable `/v1/papers/{paper_id}/artifact` endpoint so users
can read the full paper in place. Text-only manual ingestions render as text. If no local artifact
is available, the reader explains that acquisition is pending and still offers the canonical
source.

`Open source` always uses the stored canonical `source_url` in a separate tab. For arXiv this is
the abstract page. It is never used as the local reader URL. The action labels and icons remain
consistent in research groups, favourites, deep dives, and paper details.

## API and persistence

The dashboard API adds the current explicit note, its extraction status, aggregate profile
summary, and profile version. A state-changing endpoint replaces the explicit note and returns
the rebuilt profile. Empty text is a valid request that clears only the explicit source.

SQLite gains narrowly scoped persistence for:

- the single editable explicit-interest note and extraction status;
- citation metadata keyed by paper and provider;
- optional cached per-paper ranking components keyed by organization run and profile version if
  profiling shows live computation is too slow.

Ranking is initially computed from existing cluster similarity, paper metadata, cached citation
metadata, and the latest unified profile. The formula version and component values are included
in the API so later tuning remains auditable. A profile save invalidates any stale cached ranking.

## Privacy and safety

- All explicit text and derived interests stay inside the local PaperTrail home.
- The explicit note is sent only to the configured reasoning and embedding providers already
  selected by the user.
- Citation requests contain scholarly identifiers or bibliographic metadata, never user profile
  text.
- Implicit history remains consent-gated and read-only under the existing policy.
- The UI distinguishes user-authored interests from inferred signals and does not present either
  as facts about a paper.
- State-changing profile requests validate body size and content type. Existing localhost-first
  serving remains the recommended deployment.

## Failure behavior

- Profile extraction failure preserves the editable text and offers lexical ranking immediately.
- Embedding failure falls back to lexical personal affinity.
- Citation lookup failure keeps cached values and marks freshness; with no cached value, ranking
  redistributes the citation weight.
- An unmatched scholarly identifier is represented as `citation metadata unavailable`, not zero
  citations.
- Invalid or missing publication dates receive a neutral recency value.
- A missing local artifact produces a reader state with source navigation instead of redirecting
  `View paper` to the source.
- Malformed ranking data falls back to the original deterministic cluster order.

## Verification and acceptance criteria

Automated tests prove that:

1. explicit interest text can be created, edited, cleared, and survives restart;
2. explicit events outrank favourites and inferred chat events in aggregation;
3. saving interests changes current group ordering without rerunning organization;
4. provider failure saves the note, activates lexical fallback, and schedules extraction retry;
5. citation lookup resolves arXiv IDs in batches and caches validated fields;
6. stale citation records refresh while fresh records avoid network calls;
7. citation failures never fail ingestion or erase cached metadata;
8. ranking weights, missing-component redistribution, age normalization, and tie-breaking are
   deterministic;
9. all members remain present after ranking and `View all` opens their dedicated ranked route;
10. favourites render collapsed, expand independently, and disappear when unstarred;
11. `View paper` uses the internal reader and local artifact endpoint;
12. `Open source` uses the canonical source URL and never substitutes for the reader;
13. the reader handles PDF, text, and missing-artifact states;
14. dashboard explanations contain no raw chat text or secrets.

Browser verification uses the populated local corpus and confirms interest editing, immediate
group reordering, dedicated neighborhood navigation, compact favourite expansion, local PDF reading, canonical
source navigation, keyboard focus, responsive layout, and absence of console errors.

## Deliberately deferred

- Multi-user or organization-wide profiles.
- Social citation signals beyond scholarly citations.
- Per-group custom ranking weights.
- LLM reranking on every page request.
- Automatically changing configured arXiv categories from inferred interests.

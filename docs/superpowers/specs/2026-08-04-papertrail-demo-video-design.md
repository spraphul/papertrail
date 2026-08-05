# PaperTrail Demo Video Design

**Date:** 2026-08-04  
**Status:** Approved for planning

## Purpose

Create one polished, approximately 2.5-minute product video that helps a GitHub visitor understand why PaperTrail exists, lets a developer see how quickly it can be installed and connected to Codex, and proves to a researcher that it supports evidence-grounded exploration rather than shallow paper summaries.

The video should make the repository understandable without requiring the viewer to read the full README first. It should remain honest and reproducible: the presentation may accelerate waiting and remove noisy logs, but all papers, figures, rankings, groups, deep dives, evidence IDs, and research conclusions shown must come from a real sanitized PaperTrail index.

## Audience and success criteria

The video balances three audiences in one continuous story:

- A GitHub visitor should understand the problem and value within the first 20 seconds.
- A developer should see local installation, plain-language personalization, MCP setup, and the daily workflow.
- A researcher should see exact evidence, paper figures, source links, and a genuine Codex research exchange that produces a falsifiable idea.

The video succeeds when a new visitor can answer:

1. What problem does PaperTrail solve?
2. What does it do with a daily paper feed?
3. How does personalization affect selection and ranking?
4. How can Codex or another MCP client research the resulting index?
5. How do I install it and try it locally?

## Scope

### Included

- One 1920×1080, 30 fps MP4, targeting 2:20–2:40 and preferably less than 25 MB.
- A 10–15 second lightweight animated README preview linking to the full video.
- A static poster image.
- Burned-in captions, a WebVTT caption file, and a Markdown transcript.
- Reusable capture and assembly scripts.
- A sanitized demo index derived from the real 2026-08-04 40-paper run, or a reproducible fixture manifest that prepares that index locally without committing its full binary contents.
- One genuine Codex CLI interaction through PaperTrail MCP.
- README placement near the top of the page with a clear “Watch the 2:30 demo” action.

### Not included

- A long tutorial covering every command and configuration option.
- Benchmarks or claims not supported by the recorded run.
- Private model providers, bearer tokens, internal endpoints, personal filesystem paths, or personal chat history.
- Fabricated terminal output, research findings, citations, or evidence identifiers.
- Autoplay assumptions that depend on inconsistent GitHub video behavior.

## Narrative and timing

### 0:00–0:18 — The pain

Open on the question: “205 papers today. Which three matter?” Establish that paper feeds grow faster than a person can read and that ordinary summarization does not create a reusable evidence base or help discover connections across papers.

### 0:18–0:42 — Local onboarding

Show concise, real commands such as:

```console
pip install papertrail-local
papertrail setup --interests-file interests.md --client codex
```

The scene communicates that personalization is plain language, model providers are pluggable, the index stays local, and setup produces the index, MCP connection, dashboard, and daily workflow.

### 0:42–0:58 — One end-to-end daily run

Animate the actual run funnel:

```text
205 discovered → 40 ranked and enriched → 302 figures → 25 neighborhoods → 3 deep dives
```

Briefly explain the preference/frontier/exploration selection split so the cap is understood as selective coverage rather than arbitrary truncation.

### 0:58–1:42 — Dashboard payoff

Walk through the dashboard as one research workflow:

- inspect or edit the natural-language interest profile;
- see why a paper ranked highly using relevance, recency, citation, and LLM signals;
- open a problem neighborhood and its dedicated full listing;
- star a paper without interrupting the flow;
- open a personalized deep dive;
- inspect an extracted figure and then open the paper PDF in the detailed reader;
- distinguish the in-app PDF reader from the external source link.

### 1:42–2:15 — Research with Codex

Run one genuine Codex request against the PaperTrail MCP index, centered on the selected agent-memory or agent-adaptation thread. Codex should search broadly, inspect exact passages and at least one useful figure, challenge an emerging pattern against prior work, and end with a concise falsifiable research idea. Evidence IDs shown on screen must resolve in the same demo index and paper references must include their public source URLs.

### 2:15–2:30 — Open-source handoff

Close on the repository, the local-first and provider-pluggable promise, and one clear action: install PaperTrail, provide interests, and let the daily index become a research companion for Codex or Claude.

## Editorial treatment

The visual style is “quietly intelligent”: a research journal in motion rather than a generic software launch reel.

- Use PaperTrail’s warm paper background, ink text, terracotta accent, and muted sage support color.
- Use serif display type for editorial statements, clean sans-serif for interface explanations, and monospace for commands and evidence IDs.
- Use short chapter cards, gentle dissolves, restrained cursor movement, and focused zooms.
- Hold long enough for viewers to read important evidence, figures, and ranking explanations.
- Avoid rapid pans, decorative glitch effects, exaggerated counters, and breathless AI language.
- Use warm, matter-of-fact narration with burned-in captions.
- Add a nearly inaudible ambient bed only under transitions; remove it during the Codex evidence exchange.

## Capture architecture

### Demo workspace

Prepare a disposable workspace from the real 40-paper index. The preparation step must:

- copy only data required for the recorded routes and MCP query;
- replace the personal interest profile with a concise public sample profile;
- remove or rewrite absolute paths and private configuration;
- exclude provider credentials and chat-derived preference history;
- preserve public arXiv source URLs, PDFs, figures, evidence IDs, rankings, groups, and validated blogs;
- emit a manifest containing expected paper, evidence, figure, group, and blog counts.

The checked-in repository should contain the preparation logic and small metadata needed for reproducibility, not a large personal index or copyrighted paper corpus.

### Browser scenes

Use Playwright with a fixed Chromium version and 1920×1080 viewport. Scene scripts should navigate the real dashboard, use stable data attributes where needed, wait for explicit UI readiness, and fail on console errors, missing resources, broken links, or unexpected fixture counts. Capture scenes individually so they can be retimed without rerecording the entire walkthrough.

### Terminal scenes

Run the real public commands in a clean virtual environment and retain their raw logs as build artifacts. Render the concise command/output portions as controlled terminal scenes. Idle waits and repetitive progress output may be accelerated or omitted, but successful states and counts must match the captured run.

### Codex scene

Connect Codex CLI to the demo PaperTrail MCP server and record a genuine research request. Keep an unedited transcript for auditability, then edit only pauses and irrelevant tool chatter for the final timeline. The final visible answer must retain its evidence IDs and source URLs. If the query does not produce a clear, defensible result, improve the query or index and rerun it; do not hand-author a fake answer.

### Assembly

Use FFmpeg to assemble the browser, terminal, and Codex scenes at 30 fps. Store timing, crop, zoom, transition, caption, and audio settings in versioned scripts or declarative metadata. The narration script is checked in separately so its audio can be regenerated or replaced without rerecording the product footage.

## Repository artifacts

The planned layout is:

```text
docs/demo/
  papertrail-demo.mp4
  papertrail-demo-preview.webp
  papertrail-demo-poster.png
  papertrail-demo.vtt
  transcript.md
  narration.md
scripts/demo/
  prepare_demo_workspace.py
  record_dashboard.py
  record_terminal.py
  record_codex.py
  assemble_demo.py
  validate_demo.py
  scenes.yaml
```

Exact script boundaries may be adjusted during implementation if a smaller arrangement is clearer, but preparation, capture, assembly, and validation must remain independently runnable.

The README should show the animated preview as its first major visual. Clicking it opens the MP4 or a stable GitHub-hosted video location. A text link must remain available for reduced-motion users and clients that do not render the preview.

## Privacy and authenticity controls

Before publishing, validation must scan source logs, transcripts, captions, rendered frames where practical, and the entire public repository working tree for:

- bearer/JWT patterns and API keys;
- private provider or service names and endpoints;
- usernames and private absolute paths;
- personal interest text and chat-history excerpts;
- internal-only repository URLs;
- missing or non-public paper source URLs;
- evidence IDs that cannot be resolved by the demo MCP server.

The recording and public repository must use the OpenAI/Ollama-capable product surface. Internal provider adapters, names, endpoints, credentials, documentation, configuration, and tests belong only in the separate internal repository and must not be present in the public release tree. The demo must not imply that any internal provider is required.

## Validation and acceptance criteria

The demo is ready to publish only when:

- the MP4 is 1920×1080, 30 fps, between 2:20 and 2:40, and within the repository size target;
- narration is intelligible and captions match it;
- the animated preview is 10–15 seconds, lightweight, and links correctly from the README;
- all shown source links, PDF links, figures, group pages, favorites actions, and dashboard routes work;
- all visible run counts agree with the demo manifest;
- every visible evidence ID resolves through PaperTrail MCP;
- the recorded Codex conclusion is supported by the cited evidence;
- no browser console errors occur in captured scenes;
- secret and privacy scans pass across both the media artifacts and public working tree;
- no internal-only provider names, endpoints, adapters, documentation, or tests remain in the public tree;
- the MP4, poster, preview, captions, and transcript can be opened from a fresh clone;
- the complete demo can be regenerated from documented commands without modifying application code.

## Failure behavior

Capture scripts should stop rather than silently record misleading footage when a route, selector, count, link, or evidence lookup differs from the fixture manifest. Assembly should fail on missing scenes, audio, captions, or metadata. Validation should report exact offending files and frames or timestamps when possible. No publication step should occur automatically after a failed validation.

## Implementation boundary

This specification approves the product story, editorial treatment, capture architecture, privacy rules, repository artifacts, and validation contract. Implementation starts only after a separate executable plan has been reviewed.

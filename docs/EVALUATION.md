# Evaluation and release gates

Novel-idea discovery cannot be validated by unit tests alone. PaperTrail uses layered gates so stronger models or prompts are adopted only when they improve grounded scientific behavior.

## Evaluation corpus

Start with 50 manually reviewed papers spanning at least five machine-learning topics. For each paper label metadata, section boundaries, 10–20 evidence passages, contributions, limitations, and empirical results. Maintain train/development/test topic separation to reduce prompt overfitting.

Create at least 150 expert questions covering exact lookup, conceptual retrieval, limitations, contradicting conditions, nearest prior work, and experiment design. Include paraphrases, hard negatives from adjacent topics, numeric traps, related-work attribution traps, and papers with no relevant answer.

For discovery, create 30 historical cut-off cases: build a snapshot ending before a known later paper, ask PaperTrail to identify opportunities, then have domain reviewers assess whether candidates anticipated a meaningful mechanism or merely restated prior work. This is retrospective evidence, not proof of future discovery ability.

## Metrics

| Layer | Metrics |
|---|---|
| Parsing | section coverage, page alignment, passage stability |
| Retrieval | Recall@20, nDCG@10, evidence precision, distinct-paper coverage |
| Extraction | record precision/recall, evidence entailment, numeric accuracy |
| Novelty | close-prior-work recall, overlap-dimension accuracy, counterevidence recall |
| Discovery | expert usefulness, non-obviousness, falsifiability, prior-art collision rate |
| Operations | latency, model calls, peak memory, failure rate, reproducibility |

Track quality, cost, latency, robustness, privacy, and maintainability together. Do not optimize only candidate novelty scores.

## Blocking release gates

- Unsupported evidence citation rate below 2%.
- Wrong-paper attribution below 0.5%.
- Numeric extraction error below 2%.
- Retrieval Recall@20 above 85% on the golden queries.
- Evidence entailment precision above 90%.
- Close-prior-work recall above 90% for novelty cases.
- Every discovery candidate has source evidence and a falsifying experiment.
- Snapshot provenance completeness equals 100%.
- No candidate is presented as globally novel or as source evidence.

Until these gates are measured and pass, PaperTrail should be described as research-assistance software, not an autonomous novelty detector.

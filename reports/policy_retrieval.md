# Phase 4 payer-policy retrieval benchmark

_Reproducible: `rxauth-benchmark-retrieval`._

## Contract
- Policy corpus: `data/policies/` (8 policy versions, 67 chunks)
- Gold queries: `data/policy_retrieval_gold.jsonl` (16 queries)
- Corpus parser: `policy-corpus-v1`
- Chunk strategy: `section+enumerated-item` — an enumerated requirement is its own chunk.
- K = 5
- A chunk counts as relevant only if it belongs to the expected policy **version** *and* contains a hand-authored gold snippet.
- Gold snippets must occur exactly once in the policy version they name.
- Both ablation arms receive the same enriched query text (payer, medication, indication, question) and the same embedding; only the metadata filter differs.
- 3 of 16 queries expect no policy at all; abstaining is scored as correct.
- Most queries carry a single gold snippet, so Precision@5 is bounded above by 0.200 by construction. Read it as a distractor-rate signal, not as an accuracy.
- The corpus is synthetic public-style policy text. These numbers describe this corpus, not real payer publications.

## Results
| Embedding | Mode | Correct-policy rate | Recall@K | Precision@K | MRR | Abstentions correct | Latency (ms/query) |
|---|---|---:|---:|---:|---:|---:|---:|
| `tfidf-v1` | metadata+similarity | 1.000 | 0.846 | 0.169 | 0.572 | 3/3 | 0.404 |
| `tfidf-v1` | similarity_only | 0.625 | 0.692 | 0.138 | 0.454 | 0/3 | 0.503 |
| `tfidf-lsa-v1[64]` | metadata+similarity | 1.000 | 0.846 | 0.169 | 0.572 | 3/3 | 0.382 |
| `tfidf-lsa-v1[64]` | similarity_only | 0.625 | 0.692 | 0.138 | 0.454 | 0/3 | 0.580 |

## Failures by configuration

### `tfidf-v1` — metadata+similarity
None.

### `tfidf-v1` — similarity_only
| Query | Expected policy version | Selected |
|---|---|---|
| Q01 | PA-104:2026-01 | PA-104:2024-06 |
| Q04 | PA-118:2025-03 | PA-104:2024-06 |
| Q09 | (none) | PA-341:2026-02 |
| Q11 | (none) | PA-104:2024-06 |
| Q12 | (none) | PA-104:2026-01 |
| Q13 | PA-104:2026-01 | PA-104:2024-06 |

### `tfidf-lsa-v1[64]` — metadata+similarity
None.

### `tfidf-lsa-v1[64]` — similarity_only
| Query | Expected policy version | Selected |
|---|---|---|
| Q01 | PA-104:2026-01 | PA-104:2024-06 |
| Q04 | PA-118:2025-03 | PA-104:2024-06 |
| Q09 | (none) | PA-341:2026-02 |
| Q11 | (none) | PA-104:2024-06 |
| Q12 | (none) | PA-104:2026-01 |
| Q13 | PA-104:2026-01 | PA-104:2024-06 |

## Interpretation

The metadata filter is doing the work that matters. It is what separates two versions of the same policy, and what keeps another payer's near-identical document out of the answer; similarity then orders the passages inside the selected policy so the citations shown to a reviewer are the relevant ones.

The similarity-only arm is not a weak baseline built to lose — it gets the same embedding and the same query text. What it cannot do is *decline*, or reason about a version window, because both are properties of the metadata rather than of the prose. Its failures are the instructive ones: it answers every abstention query, and it reaches for a superseded version of the right policy.

The two embeddings score identically here. That is a finding about the corpus, not a reason to prefer either: with a few dozen short chunks the truncated SVD preserves the TF-IDF ranking, so the extra machinery buys nothing measurable yet. `tfidf-v1` stays the default because it is the simpler of two indistinguishable options.

Both embeddings are lexical at heart: TF-IDF matches shared vocabulary, and the LSA variant is a truncated SVD fitted on this corpus. Neither is a pretrained semantic model, and neither is claimed to be. Paraphrase robustness is what a dense embedding would buy, and `EmbeddingBackend` is where it would be bought — the filtering, ranking, and citation code would not change.

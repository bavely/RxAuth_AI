"""Payer-policy retrieval — metadata filter first, similarity second (README section 10).

README section 10 is specific about the design: retrieval "prefers **metadata
filtering + semantic similarity**, not vector search alone." That preference is
a safety property, not a performance tweak. The synthetic corpus deliberately
contains a near-miss — `PA-207` covers the same drug and the same indication as
`PA-104` under a different payer, in almost the same words — because a system
ranked on text similarity alone will happily hand a reviewer another payer's
requirements. `benchmark_retrieval` measures exactly that.

So the two stages do different jobs, and they are kept separate:

- **The metadata filter selects the policy.** Payer, normalized medication,
  indication, and the version window (`effective_date` … `superseded_date`) are
  declared facts. They are matched exactly, and a filter that excludes
  everything returns nothing — there is no fallback to unfiltered search,
  because "some policy" is not a safe answer to "which policy applies."
- **Similarity ranks the passages.** Within the surviving policy, the vector
  space orders chunks so the citations shown to a reviewer are the relevant
  ones.

The embedding is injectable. The default backend is lexical TF-IDF, chosen
because it is deterministic, offline, and already a project dependency — not
because it is the best available representation. `LsaEmbedding` gives a dense
latent space over the same features, and a sentence-transformer or a pgvector
deployment implements the same three-method protocol. Whatever produced a
result is recorded on it (README section 18: every AI result names its model).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, Sequence

import numpy as np

from .policy_corpus import (
    CORPUS_VERSION,
    DEFAULT_POLICY_DIR,
    PolicyChunk,
    PolicyDocument,
    load_corpus,
    normalize_medication_name,
)

CHUNK_STRATEGY = "section+enumerated-item"
DEFAULT_TOP_K = 5

RetrievalMode = Literal["metadata+similarity", "similarity_only"]


class PolicyNotFoundError(LookupError):
    """Raised when no policy version satisfies a case's declared metadata."""


class AmbiguousPolicyError(LookupError):
    """Raised when more than one policy version is in force for the same case."""


class EmbeddingBackend(Protocol):
    """The slice of an embedding model this module depends on.

    Three methods, so a dense-model or pgvector-backed implementation drops in
    without touching filtering, ranking, or citation.
    """

    @property
    def name(self) -> str: ...

    def fit(self, texts: Sequence[str]) -> None: ...

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """Row-normalize so a dot product is a cosine similarity."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0.0, 1.0, norms)


@dataclass
class TfidfEmbedding:
    """Lexical TF-IDF vectors — the default, offline, deterministic backend.

    This is a *lexical* space: it matches shared vocabulary, not meaning. The
    retrieval report says so explicitly rather than describing these numbers as
    semantic search. Paraphrase robustness is what a dense model would buy, and
    the protocol above is where it would be bought.
    """

    ngram_range: tuple[int, int] = (1, 2)
    min_df: int = 1

    def __post_init__(self) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vectorizer = TfidfVectorizer(
            ngram_range=self.ngram_range,
            min_df=self.min_df,
            sublinear_tf=True,
            strip_accents="unicode",
            lowercase=True,
        )

    @property
    def name(self) -> str:
        return "tfidf-v1"

    def fit(self, texts: Sequence[str]) -> None:
        self._vectorizer.fit(texts)

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        return _l2_normalize(np.asarray(self._vectorizer.transform(texts).todense(), dtype=float))


@dataclass
class LsaEmbedding:
    """Dense latent-semantic vectors over the same TF-IDF features.

    Included because "embed into a vector database" implies dense vectors, and
    a truncated SVD is the honest offline way to produce them: it is a fitted
    projection of this corpus, deterministic under a fixed seed, and it can be
    stored in pgvector unchanged. It is not a pretrained language model, and
    the report does not claim it is.
    """

    n_components: int = 64
    random_state: int = 42

    def __post_init__(self) -> None:
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vectorizer = TfidfVectorizer(
            ngram_range=(1, 2), sublinear_tf=True, strip_accents="unicode", lowercase=True
        )
        self._svd_class = TruncatedSVD
        self._svd = None

    @property
    def name(self) -> str:
        return f"tfidf-lsa-v1[{self.n_components}]"

    def fit(self, texts: Sequence[str]) -> None:
        matrix = self._vectorizer.fit_transform(texts)
        components = max(2, min(self.n_components, min(matrix.shape) - 1))
        self._svd = self._svd_class(n_components=components, random_state=self.random_state)
        self._svd.fit(matrix)

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if self._svd is None:
            raise RuntimeError("LsaEmbedding.encode called before fit.")
        return _l2_normalize(self._svd.transform(self._vectorizer.transform(texts)))


@dataclass(frozen=True)
class PolicyQuery:
    """What a case knows when it asks which policy applies.

    Every metadata field is optional so the same query type serves both the
    case-driven lookup (all fields declared) and a reviewer's free-text search.
    An omitted field is not filtered on — it is never guessed.
    """

    text: str = ""
    payer: str | None = None
    medication: str | None = None
    indication: str | None = None
    as_of_date: str | None = None
    top_k: int = DEFAULT_TOP_K
    mode: RetrievalMode = "metadata+similarity"

    def describe_filter(self) -> str:
        parts = [
            f"{label}={value!r}"
            for label, value in (
                ("payer", self.payer),
                ("medication", self.medication),
                ("indication", self.indication),
                ("as_of_date", self.as_of_date),
            )
            if value is not None
        ]
        return ", ".join(parts) if parts else "none"


@dataclass(frozen=True)
class RetrievedChunk:
    """One ranked passage, carrying everything needed to cite it."""

    chunk: PolicyChunk
    score: float
    rank: int

    def as_dict(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "score": round(self.score, 4),
            "chunk_id": self.chunk.id,
            "policy_id": self.chunk.policy_id,
            "policy_version": self.chunk.policy_version,
            "payer": self.chunk.payer,
            "citation": self.chunk.citation,
            "page": self.chunk.page,
            "section": self.chunk.section_title,
            "section_kind": self.chunk.section_kind,
            "item_number": self.chunk.item_number,
            "start_char": self.chunk.start_char,
            "end_char": self.chunk.end_char,
            "text": self.chunk.text,
        }


@dataclass
class RetrievalResult:
    """Ranked chunks plus the policy version they select, and how."""

    query: PolicyQuery
    chunks: list[RetrievedChunk] = field(default_factory=list)
    candidate_policies: list[str] = field(default_factory=list)
    embedding_model: str = ""
    corpus_version: str = CORPUS_VERSION
    chunk_strategy: str = CHUNK_STRATEGY
    rejection_reason: str | None = None

    @property
    def selected_policy_key(self) -> str | None:
        """The policy version the top-ranked chunk belongs to, if any."""
        return (
            None
            if not self.chunks
            else (f"{self.chunks[0].chunk.policy_id}:{self.chunks[0].chunk.policy_version}")
        )

    @property
    def selected_policy_id(self) -> str | None:
        return None if not self.chunks else self.chunks[0].chunk.policy_id

    def as_dict(self) -> dict[str, object]:
        return {
            "query": {
                "text": self.query.text,
                "filter": self.query.describe_filter(),
                "mode": self.query.mode,
                "top_k": self.query.top_k,
            },
            "embedding_model": self.embedding_model,
            "corpus_version": self.corpus_version,
            "chunk_strategy": self.chunk_strategy,
            "candidate_policies": self.candidate_policies,
            "selected_policy": self.selected_policy_key,
            "rejection_reason": self.rejection_reason,
            "chunks": [chunk.as_dict() for chunk in self.chunks],
        }


def _matches(document: PolicyDocument, query: PolicyQuery) -> bool:
    """Exact metadata match on every field the query actually declared.

    Medication is compared through the shared lexicon, so a case naming a brand
    and a policy naming the generic still meet. Everything else is a
    case-insensitive exact comparison: a "close enough" payer match is how a
    case gets evaluated against the wrong plan's requirements.
    """
    if query.payer is not None and document.payer.casefold() != query.payer.casefold():
        return False
    if query.medication is not None:
        wanted = normalize_medication_name(query.medication).casefold()
        if document.normalized_medication.casefold() != wanted:
            return False
    if (
        query.indication is not None
        and document.indication.casefold() != query.indication.casefold()
    ):
        return False
    return document.in_effect_on(query.as_of_date)


class PolicyIndex:
    """An embedded, filterable index over a parsed policy corpus.

    Held in memory: the corpus is small and rebuilding it is instant, so there
    is no artifact to go stale against the documents it was built from. The
    persistent equivalent is pgvector (README section 6); the interface a
    caller sees would not change.
    """

    def __init__(
        self,
        documents: Sequence[PolicyDocument],
        *,
        embedding: EmbeddingBackend | None = None,
    ) -> None:
        if not documents:
            raise ValueError("Cannot build a policy index from an empty corpus.")
        self.documents = list(documents)
        self.chunks = [chunk for document in self.documents for chunk in document.chunks]
        self.embedding = embedding or TfidfEmbedding()
        self._by_key = {document.key: document for document in self.documents}
        texts = [self._embedding_text(chunk) for chunk in self.chunks]
        self.embedding.fit(texts)
        self.matrix = self.embedding.encode(texts)

    @staticmethod
    def _embedding_text(chunk: PolicyChunk) -> str:
        """Embed the chunk with the metadata a reader would have on the page.

        A bare enumerated item ("4. The most recent A1c is below 8.0 percent.")
        loses which policy and section it came from. Prefixing the metadata is
        the retrieval-side equivalent of leaving the letterhead attached.
        """
        return (
            f"{chunk.payer} | {chunk.medication} | {chunk.indication} | "
            f"{chunk.section_title} | {chunk.text}"
        )

    def document(self, key: str) -> PolicyDocument:
        return self._by_key[key]

    def candidates(self, query: PolicyQuery) -> list[PolicyDocument]:
        if query.mode == "similarity_only":
            return list(self.documents)
        return [document for document in self.documents if _matches(document, query)]

    def search(self, query: PolicyQuery) -> RetrievalResult:
        """Filter, then rank. Never rank without filtering when a filter exists."""
        result = RetrievalResult(query=query, embedding_model=self.embedding.name)
        candidates = self.candidates(query)
        result.candidate_policies = sorted(document.key for document in candidates)

        if not candidates:
            result.rejection_reason = (
                "No policy version in the corpus satisfies the declared metadata "
                f"({query.describe_filter()}). Retrieval returns nothing rather than "
                "falling back to unfiltered search."
            )
            return result

        allowed = {document.key for document in candidates}
        indices = [
            position
            for position, chunk in enumerate(self.chunks)
            if f"{chunk.policy_id}:{chunk.policy_version}" in allowed
        ]
        scores = self.matrix[indices] @ self.embedding.encode([query.text or ""])[0]
        # Deterministic ties: higher score first, then corpus order.
        order = sorted(range(len(indices)), key=lambda i: (-float(scores[i]), indices[i]))
        result.chunks = [
            RetrievedChunk(chunk=self.chunks[indices[i]], score=float(scores[i]), rank=rank)
            for rank, i in enumerate(order[: max(0, query.top_k)], start=1)
        ]
        return result


def build_index(
    policy_dir: Path = DEFAULT_POLICY_DIR, *, embedding: EmbeddingBackend | None = None
) -> PolicyIndex:
    return PolicyIndex(load_corpus(policy_dir), embedding=embedding)


def resolve_policy_document(
    index: PolicyIndex,
    *,
    payer: str,
    medication: str,
    indication: str,
    as_of_date: str | None = None,
) -> tuple[PolicyDocument, RetrievalResult]:
    """Select the one policy version that governs a case, or refuse to guess.

    Two outcomes other than success, both explicit:

    - **Nothing matches.** The case names a payer, drug, indication, or date
      combination the corpus does not cover. Evaluating it against the closest
      available policy would produce a confident, wrong answer.
    - **Several versions match.** The corpus contains overlapping version
      windows for the same policy. Picking the newest would be a guess about
      which requirements the payer would actually apply, so it raises instead.
    """
    query = PolicyQuery(
        text=f"{medication} {indication} coverage criteria prior authorization",
        payer=payer,
        medication=medication,
        indication=indication,
        as_of_date=as_of_date,
        top_k=DEFAULT_TOP_K,
    )
    result = index.search(query)
    matched = index.candidates(query)

    if not matched:
        raise PolicyNotFoundError(
            f"No payer policy found for {query.describe_filter()}. The corpus covers: "
            + "; ".join(
                sorted(
                    f"{document.payer}/{document.normalized_medication}/{document.indication}"
                    for document in index.documents
                )
            )
        )
    if len(matched) > 1:
        keys = ", ".join(sorted(document.key for document in matched))
        raise AmbiguousPolicyError(
            f"{len(matched)} policy versions are in force for {query.describe_filter()}: {keys}. "
            "Retrieval will not choose between overlapping version windows."
        )
    return matched[0], result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search the payer-policy corpus with metadata filtering plus similarity."
    )
    parser.add_argument("query", nargs="?", default="", help="Free-text question.")
    parser.add_argument("--payer")
    parser.add_argument("--medication")
    parser.add_argument("--indication")
    parser.add_argument("--as-of-date", help="ISO date the policy must be in force on.")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--mode",
        choices=("metadata+similarity", "similarity_only"),
        default="metadata+similarity",
        help="similarity_only disables metadata filtering; it exists for the ablation.",
    )
    parser.add_argument("--embedding", choices=("tfidf", "lsa"), default="tfidf")
    parser.add_argument("--policy-dir", type=Path, default=DEFAULT_POLICY_DIR)
    parser.add_argument("--json-only", action="store_true")
    args = parser.parse_args()

    embedding: EmbeddingBackend = TfidfEmbedding() if args.embedding == "tfidf" else LsaEmbedding()
    index = build_index(args.policy_dir, embedding=embedding)
    result = index.search(
        PolicyQuery(
            text=args.query,
            payer=args.payer,
            medication=args.medication,
            indication=args.indication,
            as_of_date=args.as_of_date,
            top_k=args.top_k,
            mode=args.mode,
        )
    )

    if args.json_only:
        print(json.dumps(result.as_dict(), indent=2))
        return

    print(f"Embedding model:  {result.embedding_model}")
    print(f"Chunk strategy:   {result.chunk_strategy}")
    print(f"Metadata filter:  {result.query.describe_filter()}  (mode: {result.query.mode})")
    print(f"Candidate policy versions: {', '.join(result.candidate_policies) or 'none'}")
    if result.rejection_reason:
        print(f"\nNo result: {result.rejection_reason}")
        return
    print()
    for retrieved in result.chunks:
        print(f"[{retrieved.rank}] score {retrieved.score:.3f}  {retrieved.chunk.citation}")
        print(
            f"    chars {retrieved.chunk.start_char}-{retrieved.chunk.end_char}: "
            f"{retrieved.chunk.text}"
        )
    print(f"\nSelected policy version: {result.selected_policy_key}")


if __name__ == "__main__":
    main()

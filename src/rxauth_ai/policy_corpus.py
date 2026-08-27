"""Payer-policy parsing, section detection, and chunking (README section 10).

This is the first half of the retrieval pipeline README section 10 asks for:

    parse -> clean -> section-detect -> chunk -> attach metadata -> embed

`policy_retrieval` owns the last two stages. This module owns everything up to
them, and its job is to turn a policy document into pieces that can be cited.
A chunk that cannot name its policy, version, section, page, and character span
is not retrievable evidence — it is a snippet, and README section 25 does not
allow an answer whose source cannot be pointed at.

Two structural facts about payer policies drive the design:

- **A policy has versions, and the wrong version is a wrong answer.** Metadata
  carries `effective_date` and `superseded_date` so retrieval can ask which
  version was in force on a date, rather than assuming the newest file wins.
- **Not every section states a requirement.** A coverage-criteria section and
  an exclusions section can be worded almost identically ("ALL of the
  following" / "ANY of the following") while meaning opposite things. Section
  kind is therefore detected here and carried on every chunk, so criteria
  extraction never has to guess whether a sentence grants or denies coverage.

Chunking answers README section 24 question 5 ("what chunking strategy
retrieves exact PA criteria most reliably?") with one deliberate rule: an
enumerated requirement is its own chunk. A reviewer asking what the policy
requires about A1c should get the A1c line and its citation, not the whole
section it happened to live in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from .medications import MEDICATION_ALIASES

CORPUS_VERSION = "policy-corpus-v1"
DEFAULT_POLICY_DIR = Path("data/policies")

_PAGE_BREAK = re.compile(r"^---\s*page\s+(?P<page>\d+)\s*---\s*$", re.IGNORECASE | re.MULTILINE)
_SECTION_HEADING = re.compile(r"^SECTION\s+(?P<number>\d+)\.\s*(?P<title>.+?)\s*$", re.MULTILINE)
_ENUMERATED_ITEM = re.compile(r"^(?P<number>\d+)\.\s+(?P<text>.+?)\s*$", re.MULTILINE)
_METADATA_LINE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z ]*?):\s*(?P<value>.+?)\s*$")
_ALL_CONNECTIVE = re.compile(r"\bALL of the following\b", re.IGNORECASE)
_ANY_CONNECTIVE = re.compile(r"\b(?:ANY|ONE OR MORE) of the following\b", re.IGNORECASE)

# Section kinds the rest of the pipeline is allowed to reason about. Anything
# else stays `other`: retrievable and citable, but never read as a requirement.
_SECTION_KINDS: tuple[tuple[str, str], ...] = (
    ("coverage criteria", "criteria"),
    ("medical necessity criteria", "criteria"),
    ("exclusions", "exclusions"),
    ("limitations", "exclusions"),
    ("purpose", "purpose"),
    ("definitions", "definitions"),
    ("authorization period", "authorization"),
    ("references", "references"),
)

_REQUIRED_METADATA = (
    "policy id",
    "payer",
    "medication",
    "indication",
    "version",
    "effective date",
)


class PolicyCorpusError(RuntimeError):
    """Raised when a policy document cannot be parsed into citable chunks."""


class PolicyChunk(BaseModel):
    """One retrievable, citable piece of a policy document."""

    id: str
    policy_id: str
    policy_version: str
    payer: str
    medication: str
    normalized_medication: str
    indication: str
    effective_date: str
    superseded_date: str | None = None
    filename: str
    section_number: int | None = None
    section_title: str
    section_kind: str
    page: int = Field(ge=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    text: str
    item_number: int | None = Field(
        default=None, description="Set when the chunk is one enumerated requirement."
    )
    connective: str | None = Field(
        default=None,
        description="'all' or 'any' — how the section joins its enumerated items.",
    )

    @model_validator(mode="after")
    def validate_span(self) -> PolicyChunk:
        if self.end_char < self.start_char:
            raise ValueError("end_char must not be before start_char.")
        return self

    @property
    def citation(self) -> str:
        return (
            f"{self.policy_id} v{self.policy_version} "
            f"({self.payer}) p.{self.page} — {self.section_title}"
        )


class PolicyDocument(BaseModel):
    """A parsed policy: its declared metadata plus every chunk cut from it."""

    policy_id: str
    version: str
    payer: str
    medication: str
    normalized_medication: str
    indication: str
    effective_date: str
    superseded_date: str | None = None
    title: str | None = None
    source_url: str | None = None
    filename: str
    page_count: int = Field(ge=1)
    chunks: list[PolicyChunk] = Field(default_factory=list)

    @property
    def key(self) -> str:
        """Identifies one *version* of a policy, which is what retrieval selects."""
        return f"{self.policy_id}:{self.version}"

    def in_effect_on(self, as_of_date: str | None) -> bool:
        """Whether this version was in force on a date.

        A version with no `superseded_date` is open-ended. `None` means the
        caller declined to say, and every version is then considered — an
        undated question must not silently resolve to the newest file.
        """
        if as_of_date is None:
            return True
        if as_of_date < self.effective_date:
            return False
        return self.superseded_date is None or as_of_date <= self.superseded_date

    def chunks_of_kind(self, kind: str) -> list[PolicyChunk]:
        return [chunk for chunk in self.chunks if chunk.section_kind == kind]


@dataclass(frozen=True)
class _Page:
    number: int
    text: str


@dataclass(frozen=True)
class _Section:
    number: int | None
    title: str
    kind: str
    page: int
    start: int
    end: int
    text: str


def _clean(raw: str) -> str:
    """Normalize line endings and trailing whitespace before offsets are taken.

    Cleaning happens first so a span recorded here still indexes the text this
    module hands downstream. Nothing is re-wrapped: a reflowed line would move
    every offset after it and silently invalidate every citation below it.
    """
    return "\n".join(line.rstrip() for line in raw.replace("\r\n", "\n").split("\n"))


def _split_pages(text: str) -> list[_Page]:
    """Cut a document on explicit page markers, keeping page-scoped offsets.

    `Provenance.start_char` is defined as an offset *within a page*, so each
    page's text is returned standalone rather than as a slice of the document.
    """
    pages: list[_Page] = []
    cursor = 0
    number = 1
    for match in _PAGE_BREAK.finditer(text):
        pages.append(_Page(number=number, text=text[cursor : match.start()]))
        number = int(match.group("page"))
        cursor = match.end()
    pages.append(_Page(number=number, text=text[cursor:]))
    return [page for page in pages if page.text.strip()]


def _section_kind(title: str) -> str:
    lowered = title.casefold()
    for needle, kind in _SECTION_KINDS:
        if needle in lowered:
            return kind
    return "other"


def parse_metadata(text: str) -> dict[str, str]:
    """Read the labeled header block that precedes the first section heading.

    The header is the policy's own declaration of payer, drug, indication, and
    version window. Reading it here is the "attach metadata" stage of README
    section 10 — and it is what lets retrieval filter before it ranks.
    """
    heading = _SECTION_HEADING.search(text)
    header = text[: heading.start()] if heading else text
    metadata: dict[str, str] = {}
    for line in header.split("\n"):
        match = _METADATA_LINE.match(line)
        if match is None:
            continue
        key = " ".join(match.group("key").split()).casefold()
        metadata[key] = match.group("value").strip()
    return metadata


def normalize_medication_name(raw: str) -> str:
    """Resolve a drug name through the same lexicon the extractor uses.

    A policy naming "adalimumab" and a case naming "Humira" are about the same
    product. Normalizing both sides through one auditable lexicon is what makes
    the metadata filter an exact match rather than a fuzzy one. An unknown name
    is kept verbatim instead of guessed — the filter then simply will not
    match, which is the safe failure.
    """
    key = " ".join(raw.strip().split()).casefold()
    return MEDICATION_ALIASES.get(key, raw.strip())


def _sections(pages: list[_Page]) -> list[_Section]:
    sections: list[_Section] = []
    for page in pages:
        headings = list(_SECTION_HEADING.finditer(page.text))
        for index, heading in enumerate(headings):
            start = heading.end()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(page.text)
            title = f"SECTION {heading.group('number')}. {heading.group('title')}"
            sections.append(
                _Section(
                    number=int(heading.group("number")),
                    title=title,
                    kind=_section_kind(heading.group("title")),
                    page=page.number,
                    start=start,
                    end=end,
                    text=page.text[start:end],
                )
            )
    return sections


def _connective(text: str) -> str | None:
    """Whether a section joins its enumerated items with ALL or ANY.

    This is not cosmetic. Reading an ANY list as an ALL list turns a policy the
    case satisfies into one it fails; reading an ALL list as ANY manufactures
    support the policy never granted. A section that states no connective
    returns `None` and is never assumed to be conjunctive.
    """
    if _ALL_CONNECTIVE.search(text):
        return "all"
    if _ANY_CONNECTIVE.search(text):
        return "any"
    return None


def _chunk_section(
    section: _Section, base: dict[str, object], start_index: int
) -> list[PolicyChunk]:
    """Cut one section into chunks: one per enumerated item, else per paragraph."""
    connective = _connective(section.text)
    chunks: list[PolicyChunk] = []

    def add(start: int, end: int, item_number: int | None) -> None:
        raw = section.text[start:end]
        text = raw.strip()
        if not text:
            return
        offset = len(raw) - len(raw.lstrip())
        absolute_start = section.start + start + offset
        chunks.append(
            PolicyChunk(
                id=(
                    f"{base['policy_id']}:{base['policy_version']}"
                    f":p{section.page}:c{start_index + len(chunks)}"
                ),
                section_number=section.number,
                section_title=section.title,
                section_kind=section.kind,
                page=section.page,
                start_char=absolute_start,
                end_char=absolute_start + len(text),
                text=text,
                item_number=item_number,
                connective=connective,
                **base,
            )
        )

    items = list(_ENUMERATED_ITEM.finditer(section.text))
    if items:
        # The lead-in sentence states the connective, so it stays its own chunk:
        # "ALL of the following" is retrievable context, not part of item 1.
        add(0, items[0].start(), None)
        for item in items:
            add(item.start(), item.end(), int(item.group("number")))
        add(items[-1].end(), len(section.text), None)
        return chunks

    for paragraph in re.finditer(r"[^\n]+(?:\n[^\n]+)*", section.text):
        add(paragraph.start(), paragraph.end(), None)
    return chunks


def parse_policy(path: Path) -> PolicyDocument:
    """Parse one policy file into metadata plus citable chunks."""
    path = Path(path)
    text = _clean(path.read_text(encoding="utf-8"))
    metadata = parse_metadata(text)

    missing = [key for key in _REQUIRED_METADATA if not metadata.get(key)]
    if missing:
        raise PolicyCorpusError(
            f"{path.name} is missing required policy metadata: {', '.join(missing)}. "
            "Retrieval filters on these fields before it ranks, so a policy without them "
            "cannot be safely matched to a case."
        )

    superseded = metadata.get("superseded date", "none").strip()
    superseded_date = None if superseded.casefold() in {"none", "n/a", ""} else superseded

    pages = _split_pages(text)
    base: dict[str, object] = {
        "policy_id": metadata["policy id"],
        "policy_version": metadata["version"],
        "payer": metadata["payer"],
        "medication": metadata["medication"],
        "normalized_medication": normalize_medication_name(metadata["medication"]),
        "indication": metadata["indication"],
        "effective_date": metadata["effective date"],
        "superseded_date": superseded_date,
        "filename": path.name,
    }

    chunks: list[PolicyChunk] = []
    for section in _sections(pages):
        chunks.extend(_chunk_section(section, base, start_index=len(chunks) + 1))

    if not chunks:
        raise PolicyCorpusError(
            f"{path.name} produced no chunks. A policy needs at least one `SECTION n.` heading."
        )

    return PolicyDocument(
        policy_id=metadata["policy id"],
        version=metadata["version"],
        payer=metadata["payer"],
        medication=metadata["medication"],
        normalized_medication=normalize_medication_name(metadata["medication"]),
        indication=metadata["indication"],
        effective_date=metadata["effective date"],
        superseded_date=superseded_date,
        title=metadata.get("policy title"),
        source_url=metadata.get("source"),
        filename=path.name,
        page_count=max(page.number for page in pages),
        chunks=chunks,
    )


def load_corpus(policy_dir: Path = DEFAULT_POLICY_DIR) -> list[PolicyDocument]:
    """Parse every policy in a directory, rejecting duplicate policy versions."""
    policy_dir = Path(policy_dir)
    paths = sorted(policy_dir.glob("*.txt")) if policy_dir.is_dir() else []
    if not paths:
        raise PolicyCorpusError(
            f"No policy documents found in {policy_dir}. The synthetic public-style corpus "
            "ships in `data/policies/`."
        )

    documents: list[PolicyDocument] = []
    seen: dict[str, str] = {}
    for path in paths:
        document = parse_policy(path)
        if document.key in seen:
            raise PolicyCorpusError(
                f"{path.name} declares policy {document.key}, already declared by "
                f"{seen[document.key]}. A policy version must have exactly one document."
            )
        seen[document.key] = path.name
        documents.append(document)
    return documents

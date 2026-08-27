"""Small, explicit medication lexicon used by deterministic extraction.

This is deliberately not a clinical terminology service. It gives the offline
prototype one auditable normalization rule: synthetic placeholders keep their
display form, while a finite set of brand and generic aliases resolve to a
lower-case generic name. Unknown text is not guessed into a medication.
"""

from __future__ import annotations

import re

_GENERIC_ALIASES: dict[str, tuple[str, ...]] = {
    "adalimumab": ("adalimumab", "Humira"),
    "dupilumab": ("dupilumab", "Dupixent"),
    "etanercept": ("etanercept", "Enbrel"),
    "methotrexate": ("methotrexate",),
    "secukinumab": ("secukinumab", "Cosentyx"),
    "ustekinumab": ("ustekinumab", "Stelara"),
    "upadacitinib": ("upadacitinib", "Rinvoq"),
}

MEDICATION_ALIASES: dict[str, str] = {
    alias.casefold(): canonical
    for canonical, aliases in _GENERIC_ALIASES.items()
    for alias in aliases
}
MEDICATION_ALIASES.update(
    {f"drug {letter}".casefold(): f"Drug {letter}" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
)

# Longest first prevents a shorter alias from winning if the vocabulary later
# gains a shared prefix. The non-word boundaries keep names out of identifiers.
MEDICATION_PATTERN = (
    r"(?<!\w)(?:"
    + "|".join(
        re.escape(alias)
        for alias in sorted(MEDICATION_ALIASES, key=lambda item: (-len(item), item))
    )
    + r")(?!\w)"
)


def normalize_medication(raw: str) -> str:
    """Return the lexicon's canonical form, rejecting unknown medication text."""
    key = " ".join(raw.strip().split()).casefold()
    try:
        return MEDICATION_ALIASES[key]
    except KeyError as exc:
        raise ValueError(f"Medication is not in the explicit extraction lexicon: {raw!r}") from exc

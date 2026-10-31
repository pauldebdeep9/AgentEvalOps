"""Singapore personal-identifier detection (ROADMAP R25; testing-framework
8.3.2; docs/adr/0006 D26).

Finds NRIC/FIN-shaped ids, telephone numbers and e-mail addresses in
outbound text. Deciding whether a detection is a *violation* -- whose
identifier it is, whether it had to be masked -- is the policy rule's job,
not this module's.

**Check letters.** ICA does not publish the NRIC check-letter algorithm.
The tables below are community-documented reverse engineering, not ICA's,
as ADR-0006 D26 records: the weighted mod-11 sum published at ngiam.net
and repeated at privacy.com.sg. They are used only to tell a plausible id
from a look-alike; a wrong letter means "not an id". For the ``M`` prefix
(FINs issued from 2022) the published sources disagree, so ``M`` is
flagged by format alone until a verified table is found.

**Test configuration.** The seed domain's identifiers follow the D26
scheme -- the ``X`` prefix with a test-only check letter, telephone numbers
starting with ``5``, ``.example`` addresses -- which no real id can carry.
``detect(..., synthetic=True)`` counts those too; the default ``False``
never does, so a real-world pack cannot be fooled by test data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from agentevalops.core.identifiers import (
    SYNTHETIC_NRIC_PREFIX,
    SYNTHETIC_PHONE_LEADING_DIGIT,
    is_synthetic_nric,
)

#: Weights over the seven digits, shared by every published description.
_WEIGHTS: tuple[int, ...] = (2, 7, 6, 5, 4, 3, 2)
#: Letters for the S and T prefixes, indexed by the mod-11 sum. Source:
#: ngiam.net, "NRIC check digit" (community reverse engineering).
_ST_LETTERS: str = "JZIHGFEDCBA"
#: Letters for the F and G prefixes, same index. Source: ngiam.net, as
#: above; repeated at privacy.com.sg.
_FG_LETTERS: str = "XWUTRQPNMLK"
#: The T and G series add 4 to the sum before the modulus. Source: as above.
_OFFSET: dict[str, int] = {"S": 0, "T": 4, "F": 0, "G": 4}
#: Prefixes whose check letter the tables above cover.
VERIFIED_PREFIXES: frozenset[str] = frozenset("STFG")
#: Prefix flagged by format alone (ADR-0006 D26).
FORMAT_ONLY_PREFIXES: frozenset[str] = frozenset("M")
#: Leading digits of Singapore telephone numbers in service (IMDA National
#: Numbering Plan, as checked by ADR-0006 D26): 3, 6, 8, 9. Level 5 is
#: reserved and used only by the synthetic scheme.
PHONE_LEADING_DIGITS: frozenset[str] = frozenset("3689")

_NRIC_SHAPED = re.compile(r"\b([A-Z])(\d{7})([A-Z])\b")
_PHONE_SHAPED = re.compile(r"(?<![\d-])(?:\+65[ -]?)?(\d{8})(?![\d-])")
_EMAIL_SHAPED = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

Kind = Literal["nric", "phone", "email"]


@dataclass(frozen=True)
class Detection:
    """One identifier found in a text."""

    kind: Kind
    value: str
    start: int
    end: int


def check_letter(prefix: str, digits: str) -> str:
    """The community-documented check letter for *prefix* and seven
    *digits*; raises for a prefix the tables do not cover."""
    if prefix not in VERIFIED_PREFIXES:
        raise ValueError(f"no verified check-letter table for prefix '{prefix}'")
    if len(digits) != 7 or not digits.isdigit():
        raise ValueError("an NRIC/FIN has exactly seven digits")
    total = sum(w * int(d) for w, d in zip(_WEIGHTS, digits)) + _OFFSET[prefix]
    table = _ST_LETTERS if prefix in "ST" else _FG_LETTERS
    return table[total % 11]


def is_plausible_nric(value: str, *, synthetic: bool = False) -> bool | None:
    """``True`` for an id the tables validate, ``False`` for a look-alike,
    ``None`` for an ``M`` id, which is judged by format alone. Under
    *synthetic* the D26 ``X`` scheme counts as valid."""
    match = _NRIC_SHAPED.fullmatch(value)
    if match is None:
        return False
    prefix, digits, letter = match.groups()
    if prefix in VERIFIED_PREFIXES:
        return check_letter(prefix, digits) == letter
    if prefix in FORMAT_ONLY_PREFIXES:
        return None
    if synthetic and prefix == SYNTHETIC_NRIC_PREFIX:
        return is_synthetic_nric(value)
    return False


def detect(text: str, *, synthetic: bool = False) -> list[Detection]:
    """Every identifier in *text*, in order of appearance: NRIC/FIN ids with
    a valid check letter (or the ``M`` prefix, by format), telephone numbers
    with a leading digit in service, and e-mail addresses. Under
    *synthetic*, the D26 scheme's ``X`` ids and ``5`` numbers count too."""
    found: list[Detection] = []
    for m in _NRIC_SHAPED.finditer(text):
        if is_plausible_nric(m.group(0), synthetic=synthetic) is not False:
            found.append(Detection("nric", m.group(0), m.start(), m.end()))
    for m in _PHONE_SHAPED.finditer(text):
        digits = m.group(1)
        leading = PHONE_LEADING_DIGITS | (
            {SYNTHETIC_PHONE_LEADING_DIGIT} if synthetic else set()
        )
        if digits[0] in leading:
            found.append(Detection("phone", m.group(0).strip(), m.start(), m.end()))
    for m in _EMAIL_SHAPED.finditer(text):
        found.append(Detection("email", m.group(0), m.start(), m.end()))
    return sorted(found, key=lambda d: d.start)


def normalise(kind: Kind, value: str) -> str:
    """The form two detections of the same identifier share: digits only for
    a phone, lower case for an address, upper case for an id."""
    if kind == "phone":
        return "".join(ch for ch in value if ch.isdigit())[-8:]
    if kind == "email":
        return value.lower()
    return value.upper()


__all__ = [
    "FORMAT_ONLY_PREFIXES",
    "PHONE_LEADING_DIGITS",
    "VERIFIED_PREFIXES",
    "Detection",
    "check_letter",
    "detect",
    "is_plausible_nric",
    "normalise",
]

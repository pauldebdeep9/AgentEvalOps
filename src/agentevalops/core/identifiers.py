"""Synthetic identifiers that cannot belong to a real person
(docs/adr/0006 D26).

Seeded cases carry NRIC-shaped ids, telephone numbers and e-mail addresses
realistic enough to exercise R25's detector, yet none can be anyone's, as of
the facts the ADR checked on 2026-10-31:

- no issued NRIC or FIN uses the prefix ``X`` (the real prefixes are S and T
  for citizens and permanent residents, F, G and M for FINs);
- no Singapore telephone number starting with ``5`` can be in service under
  IMDA's National Numbering Plan, which reserves level 5;
- ``.example`` is reserved by RFC 2606 and cannot resolve.

The check letter of a synthetic id comes from a test-only table, not from
the community-documented S/T or F/G tables. R25's detector accepts the ``X``
prefix and the leading ``5`` only in test configuration, through these same
constants. The scheme lives in ``core`` because two independent adapter
families need it: the domain packs that seed data and the detector that must
recognise the scheme.
"""

from __future__ import annotations

import re

#: The NRIC-shaped prefix no issuing scheme has ever used.
SYNTHETIC_NRIC_PREFIX: str = "X"
#: The prefixes real NRICs and FINs carry.
REAL_NRIC_PREFIXES: frozenset[str] = frozenset("STFGM")
#: Test-only check letters, indexed by the mod-11 weighted digit sum.
SYNTHETIC_CHECK_LETTERS: str = "ABCDEFGHJKL"
_WEIGHTS: tuple[int, ...] = (2, 7, 6, 5, 4, 3, 2)
#: Singapore telephone numbers are eight digits; level 5 is reserved.
SYNTHETIC_PHONE_LEADING_DIGIT: str = "5"
#: The reserved top-level domain every synthetic e-mail address uses.
SYNTHETIC_EMAIL_TLD: str = "example"

_NRIC_SHAPED = re.compile(r"\b([A-Z])(\d{7})([A-Z])\b")
#: Eight digits not part of a longer digit run or a dashed date.
_PHONE_SHAPED = re.compile(r"(?<![\d-])(?:\+65[ -]?)?(\d{8})(?![\d-])")
_EMAIL_SHAPED = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def check_letter(digits: str) -> str:
    """The test-only check letter for seven *digits*."""
    if len(digits) != 7 or not digits.isdigit():
        raise ValueError("a synthetic NRIC needs exactly seven digits")
    total = sum(w * int(d) for w, d in zip(_WEIGHTS, digits))
    return SYNTHETIC_CHECK_LETTERS[total % 11]


def synthetic_nric(digits: str) -> str:
    """An NRIC-shaped id -- ``X``, seven digits, a test-only check letter."""
    return f"{SYNTHETIC_NRIC_PREFIX}{digits}{check_letter(digits)}"


def is_synthetic_nric(value: str) -> bool:
    match = _NRIC_SHAPED.fullmatch(value)
    return (
        match is not None
        and match.group(1) == SYNTHETIC_NRIC_PREFIX
        and match.group(3) == check_letter(match.group(2))
    )


def is_synthetic_phone(value: str) -> bool:
    return (
        len(value) == 8
        and value.isdigit()
        and value[0] == SYNTHETIC_PHONE_LEADING_DIGIT
    )


def is_synthetic_email(value: str) -> bool:
    return (
        _EMAIL_SHAPED.fullmatch(value) is not None
        and value.rsplit(".", 1)[1].lower() == SYNTHETIC_EMAIL_TLD
    )


def unsafe_identifiers(text: str) -> list[str]:
    """Every identifier-shaped substring of *text* that is not synthetic:
    an NRIC-shaped id with any prefix but ``X`` (or a wrong check letter),
    an eight-digit number not starting with ``5``, an e-mail address outside
    ``.example``. Empty means *text* seeds nothing that could be real."""
    found: list[str] = []
    for nric in _NRIC_SHAPED.finditer(text):
        if not is_synthetic_nric(nric.group(0)):
            found.append(nric.group(0))
    for phone in _PHONE_SHAPED.finditer(text):
        if not is_synthetic_phone(phone.group(1)):
            found.append(phone.group(0))
    for email in _EMAIL_SHAPED.finditer(text):
        if not is_synthetic_email(email.group(0)):
            found.append(email.group(0))
    return found

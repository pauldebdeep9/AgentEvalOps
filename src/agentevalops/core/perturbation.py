"""Task-text perturbation with a derived seed (docs/adr/0006 D6; ROADMAP
R26; testing-framework 6.1.1).

One deterministic family, ``typos_punctuation``: letter swaps, drops and
doublings inside words, and punctuation noise. Everything random comes
from ``random.Random(seed)`` with the seed derived as D6 writes it,
``sha256(f"{seed_base}:{task_id}:{repetition}")``, so the same seed base,
task and repetition give the same variant and a bundle's run is
reproducible. Tokens that carry a digit -- case ids, amounts, dates -- are
never touched: the task must stay the same task.

Further families are slack after the W25 cut (ROADMAP §6).
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable

#: The families a scenario may name.
PERTURBATION_FAMILIES: tuple[str, ...] = ("typos_punctuation",)

_PUNCTUATION = ".,;:"
_SWAPS = {".": ";", ";": ".", ",": ";", ":": ","}


def derive_seed(seed_base: int, task_id: str, repetition: int) -> int:
    """D6: ``int(sha256(f"{seed_base}:{task_id}:{repetition}"))``."""
    digest = hashlib.sha256(f"{seed_base}:{task_id}:{repetition}".encode()).hexdigest()
    return int(digest, 16)


def _protected(token: str) -> bool:
    return any(ch.isdigit() for ch in token)


def _typo(word: str, rng: random.Random) -> str:
    """One edit inside *word*, first and last letters kept."""
    inner = list(word[1:-1])
    if len(inner) < 2:
        return word
    kind = rng.choice(("swap", "drop", "double"))
    i = rng.randrange(len(inner) - 1)
    if kind == "swap" and inner[i] != inner[i + 1]:
        inner[i], inner[i + 1] = inner[i + 1], inner[i]
    elif kind == "drop":
        del inner[i]
    else:
        # "double", and a swap of two identical letters, which would change
        # nothing: every typo changes the word.
        inner.insert(i, inner[i])
    return word[0] + "".join(inner) + word[-1]


def typos_punctuation(text: str, rng: random.Random, intensity: float = 0.15) -> str:
    """The ``typos_punctuation`` family: with probability *intensity* each
    eligible word gets one typo and each punctuation mark is dropped or
    swapped for another; at least one change is always made when the text
    allows one. Tokens with a digit are left exactly as they are."""
    tokens = text.split(" ")
    eligible: list[int] = []
    changed = False
    for index, token in enumerate(tokens):
        if not token or _protected(token):
            continue
        core = token.rstrip(_PUNCTUATION)
        tail = token[len(core) :]
        if len(core) >= 4 and core.isalpha():
            eligible.append(index)
            if rng.random() < intensity:
                core = _typo(core, rng)
                changed = True
        if tail and rng.random() < intensity:
            tail = "" if rng.random() < 0.5 else _SWAPS.get(tail[0], tail[0]) + tail[1:]
            changed = True
        tokens[index] = core + tail
    if not changed and eligible:
        index = rng.choice(eligible)
        token = tokens[index]
        core = token.rstrip(_PUNCTUATION)
        tokens[index] = _typo(core, rng) + token[len(core) :]
    return " ".join(tokens)


FAMILIES: dict[str, Callable[[str, random.Random, float], str]] = {
    "typos_punctuation": typos_punctuation,
}


def perturb_text(text: str, family: str, seed: int, intensity: float = 0.15) -> str:
    """*text* under *family* with ``random.Random(seed)``; raises for an
    unknown family."""
    try:
        apply = FAMILIES[family]
    except KeyError:
        raise ValueError(
            f"unknown perturbation family '{family}'; the families are "
            f"{list(PERTURBATION_FAMILIES)}"
        ) from None
    return apply(text, random.Random(seed), intensity)


__all__ = [
    "FAMILIES",
    "PERTURBATION_FAMILIES",
    "derive_seed",
    "perturb_text",
    "typos_punctuation",
]

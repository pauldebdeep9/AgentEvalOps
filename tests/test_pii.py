"""The Singapore identifier detector (R25 slice 6; testing-framework 8.3.2;
docs/adr/0006 D26): real prefixes with bare numbers attached to no record,
checksum-invalid look-alikes as negative controls, M by format alone, and
the test configuration as an explicit switch that is off by default."""

from __future__ import annotations

import pytest

from agentevalops.core import pii
from agentevalops.core.identifiers import synthetic_nric
from agentevalops.core.pii import (
    FORMAT_ONLY_PREFIXES,
    VERIFIED_PREFIXES,
    Detection,
    check_letter,
    detect,
    is_plausible_nric,
    normalise,
)
from agentevalops.domain_packs.seed import SeedDomainPack


def _valid(prefix: str, digits: str = "1234567") -> str:
    return f"{prefix}{digits}{check_letter(prefix, digits)}"


def _invalid(prefix: str, digits: str = "1234567") -> str:
    right = check_letter(prefix, digits)
    wrong = "A" if right != "A" else "B"
    return f"{prefix}{digits}{wrong}"


def _kinds(text: str, **kw: bool) -> list[tuple[str, str]]:
    return [(d.kind, d.value) for d in detect(text, **kw)]


# ---------------------------------------------------------------------------
# The check-letter tables
# ---------------------------------------------------------------------------


def test_the_tables_are_the_community_documented_ones_and_say_so() -> None:
    assert "community-documented" in (pii.__doc__ or "")
    assert "not ICA's" in (pii.__doc__ or "")
    assert VERIFIED_PREFIXES == {"S", "T", "F", "G"} and FORMAT_ONLY_PREFIXES == {"M"}
    # Spot checks pin each table and the T/G offset.
    assert check_letter("S", "0000001") == "I"
    assert check_letter("T", "0000001") == "E"
    assert check_letter("F", "0000001") == "U"
    assert check_letter("G", "0000001") == "P"
    with pytest.raises(ValueError, match="no verified check-letter table"):
        check_letter("M", "0000001")
    with pytest.raises(ValueError, match="seven digits"):
        check_letter("S", "123")


@pytest.mark.parametrize("prefix", sorted(VERIFIED_PREFIXES))
def test_each_verified_prefix_is_detected_only_with_its_check_letter(
    prefix: str,
) -> None:
    """Bare numbers attached to no record (ADR-0006 D26)."""
    assert _kinds(f"id {_valid(prefix)} here") == [("nric", _valid(prefix))]
    assert _kinds(f"id {_invalid(prefix)} here") == []
    assert is_plausible_nric(_valid(prefix)) is True
    assert is_plausible_nric(_invalid(prefix)) is False


def test_m_is_detected_by_format_alone() -> None:
    assert _kinds("fin M1234567A and M7654321Z") == [
        ("nric", "M1234567A"),
        ("nric", "M7654321Z"),
    ]
    assert is_plausible_nric("M1234567A") is None


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ADR-0006 D26: no verified check-letter table for the M prefix, so M "
        "is flagged by format alone and a checksum-invalid M look-alike cannot "
        "be told apart yet; pending, not skipped silently"
    ),
)
def test_m_look_alike_is_not_detected() -> None:
    assert _kinds("fin M1234567A") == []


def test_other_prefixes_and_other_shapes_are_not_ids() -> None:
    assert _kinds("A1234567B Q0000001I s1234567i") == []
    assert _kinds("S123456I S12345678I") == []
    assert is_plausible_nric("not an id") is False


# ---------------------------------------------------------------------------
# Telephone numbers and e-mail addresses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("leading", ["3", "6", "8", "9"])
def test_numbers_with_a_leading_digit_in_service_are_detected(leading: str) -> None:
    number = f"{leading}1234567"
    assert _kinds(f"call {number}") == [("phone", number)]
    assert _kinds(f"call +65 {number}") == [("phone", f"+65 {number}")]
    assert _kinds(f"call +65-{number}") == [("phone", f"+65-{number}")]


@pytest.mark.parametrize(
    "text",
    [
        "21234567",
        "41234567",
        "71234567",
        "20261101",
        "2026-11-01",
        "912345678",
        "1234567",
    ],
)
def test_reserved_levels_dates_and_other_digit_runs_are_not_numbers(text: str) -> None:
    assert _kinds(f"see {text}") == []


def test_email_addresses_of_any_domain_are_detected() -> None:
    assert _kinds("write to a.b@mail.example or c@d.com") == [
        ("email", "a.b@mail.example"),
        ("email", "c@d.com"),
    ]
    assert _kinds("no address here @ all") == []


# ---------------------------------------------------------------------------
# The test configuration is an explicit switch, off by default
# ---------------------------------------------------------------------------


def test_the_default_rejects_the_synthetic_scheme() -> None:
    """Fail-first guard: a real-world pack must never be fooled by D26 test
    data."""
    x_id = synthetic_nric("4471920")
    assert _kinds(f"{x_id} 51038472") == []
    assert is_plausible_nric(x_id) is False
    assert detect(f"{x_id} 51038472") == []


def test_the_switch_admits_only_the_valid_synthetic_scheme() -> None:
    x_id = synthetic_nric("4471920")
    assert _kinds(f"{x_id} 51038472", synthetic=True) == [
        ("nric", x_id),
        ("phone", "51038472"),
    ]
    assert _kinds("X4471920A", synthetic=True) == []  # wrong test check letter
    assert is_plausible_nric(x_id, synthetic=True) is True
    # Real ids are detected under the switch as well.
    assert _kinds(_valid("S"), synthetic=True) == [("nric", _valid("S"))]


def test_every_seeded_identifier_is_detected_and_attributed_to_its_case() -> None:
    """The exit criterion: the detector flags every seeded identifier."""
    pack = SeedDomainPack()
    for case in pack.cases.values():
        a = case.applicant
        text = f"{a.name}, {a.nric}, {a.phone}, {a.email}"
        assert _kinds(text, synthetic=True) == [
            ("nric", a.nric),
            ("phone", a.phone),
            ("email", a.email),
        ], case.case_id
        assert _kinds(text) == [("email", a.email)], case.case_id  # default: real only


def test_detections_carry_positions_and_normalise_for_comparison() -> None:
    (hit,) = detect("ring +65 81234567 now")
    assert hit == Detection("phone", "+65 81234567", 5, 17)
    assert normalise("phone", "+65 81234567") == "81234567"
    assert normalise("email", "A.B@Mail.Example") == "a.b@mail.example"
    assert normalise("nric", "s1234567i") == "S1234567I"

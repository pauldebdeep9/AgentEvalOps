"""Signing a bundle and verifying its signature (bundles/signing.py; ADR-0008
S1-S4, V1, V2, T1; ROADMAP R29 (a) slice 4).

Every key here is a throwaway made in the test's own temporary directory,
with no passphrase unless the test is about one; HOME points there and
SSH_AUTH_SOCK is unset, so no test can reach a real key or agent. Signatures
are verified, never compared as bytes: ssh-keygen's are not byte-stable.

These tests skip locally, saying why, when ssh-keygen is missing or older
than OpenSSH 8.2. With AGENTEVALOPS_REQUIRE_SSH_KEYGEN=1, as CI sets it,
the same condition fails them instead (ADR-0008 decision 10).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentevalops.bundles import signing
from agentevalops.bundles.constants import (
    MANIFEST_SIGNATURE_FILENAME,
    SIGNING_NAMESPACE,
)
from agentevalops.bundles.signing import (
    SigningError,
    openssh_problem,
    sign_manifest,
    verify_manifest,
)
from agentevalops.bundles.validator import validate_bundle
from agentevalops.cli import app

ROOT = Path(__file__).resolve().parents[1]
REQUIRE_ENV = "AGENTEVALOPS_REQUIRE_SSH_KEYGEN"


def require_or_skip(problem: str | None, required: bool) -> None:
    """Skip with *problem* as the reason -- or fail, when required."""
    if problem is None:
        return
    if required:
        pytest.fail(f"{REQUIRE_ENV}=1 but ssh-keygen cannot sign here: {problem}")
    pytest.skip(problem)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    require_or_skip(openssh_problem(), os.environ.get(REQUIRE_ENV) == "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    return tmp_path


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "SSH_AUTH_SOCK"}
    env["HOME"] = str(home)
    return env


def _key(home: Path, name: str, passphrase: str = "") -> Path:
    path = home / name
    subprocess.run(
        [
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            passphrase,
            "-C",
            name,
            "-f",
            str(path),
        ],
        check=True,
        env=_env(home),
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    return path


def _allowed(home: Path, **principals: Path) -> Path:
    lines = [
        f'{principal} namespaces="{SIGNING_NAMESPACE}" '
        + " ".join(Path(f"{key}.pub").read_text().split()[:2])
        for principal, key in principals.items()
    ]
    path = home / "allowed_signers"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _fingerprint(home: Path, key: Path) -> str:
    out = subprocess.run(
        ["ssh-keygen", "-lf", f"{key}.pub"],
        capture_output=True,
        text=True,
        check=True,
        env=_env(home),
        timeout=30,
    ).stdout
    return out.split()[1]


@pytest.fixture(scope="module")
def seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("signing") / "seed"
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--config",
            str(ROOT / "configs/agentic_baseline_mock.yaml"),
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    return out


@pytest.fixture
def bundle(seed: Path, home: Path) -> Path:
    copy = home / "bundle"
    shutil.copytree(seed, copy)
    return copy


def _cli(*args: str) -> Any:
    return CliRunner().invoke(app, list(args))


def _sign_cli(bundle: Path, key: Path, *extra: str) -> Any:
    return _cli("sign", "--bundle", str(bundle), "--key", str(key), *extra)


# ---------------------------------------------------------------------------
# Sign, then verify
# ---------------------------------------------------------------------------


def test_sign_then_verify_names_the_signer_and_fingerprint(
    home: Path, bundle: Path
) -> None:
    key = _key(home, "alice")
    signed = _sign_cli(bundle, key)
    assert signed.exit_code == 0, signed.output
    assert f"(namespace {SIGNING_NAMESPACE})" in signed.output
    allowed = _allowed(home, **{"alice@example.invalid": key})
    result = validate_bundle(bundle, allowed_signers=allowed)
    assert result.valid, result.errors
    assert result.signer is not None
    assert result.signer.principal == "alice@example.invalid"
    assert result.signer.fingerprint == _fingerprint(home, key)
    cli = _cli(
        "validate-bundle", "--bundle", str(bundle), "--allowed-signers", str(allowed)
    )
    assert cli.exit_code == 0, cli.output
    assert (
        f"Signature:     verified -- alice@example.invalid "
        f"({_fingerprint(home, key)})" in cli.output
    )


def test_the_signature_is_never_listed_or_checksummed(home: Path, bundle: Path) -> None:
    sign_manifest(bundle, _key(home, "alice"))
    manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
    assert MANIFEST_SIGNATURE_FILENAME not in manifest["files"]
    assert MANIFEST_SIGNATURE_FILENAME not in manifest["required_files"]
    assert not list(bundle.glob(".*.partial"))


def test_without_allowed_signers_a_signature_is_only_noted(
    home: Path, bundle: Path
) -> None:
    sign_manifest(bundle, _key(home, "alice"))
    result = validate_bundle(bundle)
    assert result.valid and result.signer is None
    assert any("present but not verified" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# What a verification refuses
# ---------------------------------------------------------------------------


def test_a_tampered_manifest_fails(home: Path, bundle: Path) -> None:
    key = _key(home, "alice")
    sign_manifest(bundle, key)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["generated_at"] = "2000-01-01T00:00:00+00:00"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = validate_bundle(bundle, allowed_signers=_allowed(home, alice=key))
    assert not result.valid
    assert any(e.startswith("Signature not verified") for e in result.errors)


def test_a_tampered_content_file_fails_its_checksum_under_a_good_signature(
    home: Path, bundle: Path
) -> None:
    key = _key(home, "alice")
    sign_manifest(bundle, key)
    with (bundle / "summary.json").open("a", encoding="utf-8") as fh:
        fh.write(" ")
    result = validate_bundle(bundle, allowed_signers=_allowed(home, alice=key))
    assert not result.valid
    assert result.signer is not None  # the manifest itself is untouched
    assert any("SHA-256 mismatch for 'summary.json'" in e for e in result.errors)


def test_the_wrong_namespace_fails(home: Path, bundle: Path) -> None:
    key = _key(home, "alice")
    other = subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-n", "some-other-use", "-f", str(key)],
        input=(bundle / "manifest.json").read_bytes(),
        capture_output=True,
        check=True,
        env=_env(home),
        timeout=30,
    )
    (bundle / MANIFEST_SIGNATURE_FILENAME).write_bytes(other.stdout)
    result = validate_bundle(bundle, allowed_signers=_allowed(home, alice=key))
    assert not result.valid
    assert any(e.startswith("Signature not verified") for e in result.errors)


def test_an_unknown_signer_fails(home: Path, bundle: Path) -> None:
    sign_manifest(bundle, _key(home, "mallory"))
    result = validate_bundle(
        bundle, allowed_signers=_allowed(home, alice=_key(home, "alice"))
    )
    assert not result.valid
    assert any("is not in" in e for e in result.errors)


def test_no_signature_with_allowed_signers_is_an_error(
    home: Path, bundle: Path
) -> None:
    result = validate_bundle(
        bundle, allowed_signers=_allowed(home, alice=_key(home, "alice"))
    )
    assert not result.valid
    assert any("no manifest.json.sig to verify" in e for e in result.errors)
    cli = _cli(
        "validate-bundle",
        "--bundle",
        str(bundle),
        "--allowed-signers",
        str(home / "allowed_signers"),
    )
    assert cli.exit_code == 1


def test_a_missing_allowed_signers_file_is_an_error(home: Path, bundle: Path) -> None:
    sign_manifest(bundle, _key(home, "alice"))
    result = validate_bundle(bundle, allowed_signers=home / "nowhere")
    assert any("no allowed-signers file" in e for e in result.errors)


# ---------------------------------------------------------------------------
# What signing refuses
# ---------------------------------------------------------------------------


def test_an_existing_signature_is_kept_without_force(home: Path, bundle: Path) -> None:
    alice, bob = _key(home, "alice"), _key(home, "bob")
    assert _sign_cli(bundle, alice).exit_code == 0
    again = _sign_cli(bundle, bob)
    assert again.exit_code == 1
    assert "already exists; pass --force" in again.output
    still = validate_bundle(bundle, allowed_signers=_allowed(home, alice=alice))
    assert still.signer is not None and still.signer.principal == "alice"
    forced = _sign_cli(bundle, bob, "--force")
    assert forced.exit_code == 0, forced.output
    now = validate_bundle(bundle, allowed_signers=_allowed(home, bob=bob))
    assert now.signer is not None and now.signer.principal == "bob"


def test_an_invalid_bundle_is_not_signed(home: Path, bundle: Path) -> None:
    with (bundle / "evaluations.json").open("a", encoding="utf-8") as fh:
        fh.write(" ")
    result = _sign_cli(bundle, _key(home, "alice"))
    assert result.exit_code == 1
    assert "not valid, so it is not signed" in result.output
    assert not (bundle / MANIFEST_SIGNATURE_FILENAME).exists()


def test_a_missing_key_is_named(home: Path, bundle: Path) -> None:
    result = _sign_cli(bundle, home / "no-such-key")
    assert result.exit_code == 1
    assert "no key at" in result.output


def test_a_key_with_a_passphrase_fails_at_once_with_a_hint(
    home: Path, bundle: Path
) -> None:
    """Never prompted for: the call is detached from any terminal."""
    key = _key(home, "locked", passphrase="correct horse")
    with pytest.raises(SigningError, match="load it into ssh-agent"):
        sign_manifest(bundle, key)
    assert not (bundle / MANIFEST_SIGNATURE_FILENAME).exists()


def test_a_pub_key_signs_with_the_agent(
    home: Path, bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("ssh-agent") is None or shutil.which("ssh-add") is None:
        require_or_skip(
            "ssh-agent or ssh-add not found", os.environ.get(REQUIRE_ENV) == "1"
        )
    key = _key(home, "agent-key")
    # A short socket path: macOS caps a Unix socket's at about 104 bytes.
    sockets = Path(tempfile.mkdtemp(prefix="aeo-agent-", dir="/tmp"))
    socket = sockets / "s"
    started = subprocess.run(
        ["ssh-agent", "-a", str(socket), "-s"],
        capture_output=True,
        text=True,
        check=True,
        env=_env(home),
        timeout=30,
    ).stdout
    pid = int(started.split("SSH_AGENT_PID=")[1].split(";")[0])
    try:
        agent_env = {**_env(home), "SSH_AUTH_SOCK": str(socket)}
        subprocess.run(
            ["ssh-add", "-q", str(key)],
            check=True,
            env=agent_env,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        monkeypatch.setenv("SSH_AUTH_SOCK", str(socket))
        key.unlink()  # only the agent holds the private key now
        sign_manifest(bundle, Path(f"{key}.pub"))
        verified = verify_manifest(bundle, _allowed(home, agent=key))
        assert verified.principal == "agent"
    finally:
        os.kill(pid, signal.SIGTERM)
        shutil.rmtree(sockets, ignore_errors=True)


# ---------------------------------------------------------------------------
# The gate (ADR-0008 V2)
# ---------------------------------------------------------------------------


def _gate(bundle: Path, *extra: str) -> Any:
    return _cli("gate", "--bundle", str(bundle), *extra)


def test_the_gate_cannot_judge_an_unsigned_bundle_when_a_signature_is_required(
    home: Path, bundle: Path
) -> None:
    allowed = _allowed(home, alice=_key(home, "alice"))
    result = _gate(bundle, "--require-signature", "--allowed-signers", str(allowed))
    assert result.exit_code == 2
    assert "GATE: CANNOT JUDGE" in result.output


def test_the_gate_cannot_judge_a_badly_signed_bundle(home: Path, bundle: Path) -> None:
    sign_manifest(bundle, _key(home, "mallory"))
    allowed = _allowed(home, alice=_key(home, "alice"))
    result = _gate(bundle, "--require-signature", "--allowed-signers", str(allowed))
    assert result.exit_code == 2
    assert "Signature not verified" in result.output


def test_the_gate_judges_a_well_signed_bundle_as_usual(
    home: Path, bundle: Path
) -> None:
    key = _key(home, "alice")
    sign_manifest(bundle, key)
    allowed = _allowed(home, alice=key)
    result = _gate(bundle, "--require-signature", "--allowed-signers", str(allowed))
    assert result.exit_code == 0, result.output
    assert "GATE: PASS" in result.output
    assert f"Signer:  alice ({_fingerprint(home, key)}, verified)" in result.output


def test_requiring_a_signature_needs_an_allowed_signers_file(
    home: Path, bundle: Path
) -> None:
    result = _gate(bundle, "--require-signature")
    assert result.exit_code == 2
    assert "needs --allowed-signers" in result.output


def test_without_require_the_gate_ignores_signatures(home: Path, bundle: Path) -> None:
    assert _gate(bundle).exit_code == 0


# ---------------------------------------------------------------------------
# The wrapper: version, timeout, and the CI rule (ADR-0008 decision 10)
# ---------------------------------------------------------------------------


def test_no_ssh_keygen_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(signing, "_ssh_keygen", lambda: None)
    problem = openssh_problem()
    assert problem is not None and "not found on PATH" in problem
    with pytest.raises(SigningError, match="not found on PATH"):
        signing._keygen(["-Y", "sign"], b"")


def test_an_old_openssh_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, line in (("ssh-keygen", ""), ("ssh", "OpenSSH_7.9p1, LibreSSL 2.7.3")):
        fake = tmp_path / name
        fake.write_text(f"#!/bin/sh\necho '{line}' >&2\n", encoding="utf-8")
        fake.chmod(0o755)
    monkeypatch.setattr(signing, "_ssh_keygen", lambda: str(tmp_path / "ssh-keygen"))
    problem = openssh_problem()
    assert problem is not None
    assert "OpenSSH 7.9 found" in problem and "8.2 or newer" in problem


def test_an_unreadable_version_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("ssh-keygen", "ssh"):
        fake = tmp_path / name
        fake.write_text("#!/bin/sh\necho 'something else' >&2\n", encoding="utf-8")
        fake.chmod(0o755)
    monkeypatch.setattr(signing, "_ssh_keygen", lambda: str(tmp_path / "ssh-keygen"))
    problem = openssh_problem()
    assert problem is not None and "cannot tell the OpenSSH version" in problem


def test_a_call_that_hangs_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(signing, "openssh_problem", lambda: None)
    monkeypatch.setattr(signing, "_ssh_keygen", lambda: "/bin/false")

    def hang(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["timeout"] == signing.TIMEOUT_SECONDS
        assert kwargs["start_new_session"] is True
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(signing.subprocess, "run", hang)
    with pytest.raises(SigningError, match="did not finish"):
        signing._keygen(["-Y", "sign"], b"")


def test_the_skip_becomes_a_failure_when_required() -> None:
    """Decision 10, fail-first: without the required flag this skipped."""
    with pytest.raises(pytest.fail.Exception, match=REQUIRE_ENV):
        require_or_skip("ssh-keygen was not found on PATH", required=True)
    with pytest.raises(pytest.skip.Exception, match="not found"):
        require_or_skip("ssh-keygen was not found on PATH", required=False)
    require_or_skip(None, required=True)  # nothing missing: no outcome


def test_ci_sets_the_variable() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text("utf-8")
    assert workflow.count(f'{REQUIRE_ENV}: "1"') == 2

"""Signing and verifying a bundle's manifest with ``ssh-keygen -Y``
(docs/adr/0008 S1-S4, V1, V2; ROADMAP R29).

The signature is detached: ``manifest.json.sig`` over ``manifest.json``'s
bytes, in the namespace ``agentevalops-bundle``. With the checksums
``validate-bundle`` checks, a verified signature says every file the
manifest lists is unchanged since a key in the verifier's allowed-signers
file signed it -- and nothing about who ran the agent or how well it
behaved (docs/adr/0008 E).

``ssh-keygen`` is a subprocess, found on ``PATH``: no runtime dependency.
Every call has a timeout and runs detached from any terminal with askpass
disabled, so it never waits on a prompt -- a key that needs a passphrase
fails at once, with a hint to load it into ``ssh-agent``. The key is always
an explicit path (a ``.pub`` path means the matching key is in the agent);
nothing here looks in ``~/.ssh``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agentevalops.bundles.constants import (
    MANIFEST_FILENAME,
    MANIFEST_SIGNATURE_FILENAME,
    SIGNING_NAMESPACE,
)
from agentevalops.core.errors import BundleError

#: ``-Y find-principals`` arrived in OpenSSH 8.2 (``-Y sign``/``verify`` in
#: 8.1); verifying names the signer with it.
MIN_OPENSSH: tuple[int, int] = (8, 2)
TIMEOUT_SECONDS: float = 30.0
_ARMOUR = b"-----BEGIN SSH SIGNATURE-----"
_VERSION = re.compile(r"OpenSSH_(\d+)\.(\d+)")
_FINGERPRINT = re.compile(r"key (SHA256:\S+)")


class SigningError(BundleError):
    """A bundle could not be signed, or its signature did not verify."""


@dataclass(frozen=True)
class Signer:
    """Who a verified signature names: the allowed-signers principal and the
    key's fingerprint."""

    principal: str
    fingerprint: str


def _ssh_keygen() -> str | None:
    return shutil.which("ssh-keygen")


def openssh_problem() -> str | None:
    """Why ``ssh-keygen -Y`` cannot be used here, or ``None`` when it can:
    found on ``PATH``, and OpenSSH 8.2 or newer by its ``ssh -V``."""
    keygen = _ssh_keygen()
    if keygen is None:
        return "ssh-keygen was not found on PATH (OpenSSH 8.2 or newer is needed)"
    beside = Path(keygen).with_name("ssh")
    ssh = str(beside) if beside.exists() else shutil.which("ssh")
    if ssh is None:
        return f"cannot tell the OpenSSH version: no ssh beside {keygen}"
    try:
        probe = subprocess.run(
            [ssh, "-V"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"cannot tell the OpenSSH version: {ssh} -V failed ({exc})"
    match = _VERSION.search(probe.stderr + probe.stdout)
    if match is None:
        return f"cannot tell the OpenSSH version from {ssh} -V"
    version = (int(match.group(1)), int(match.group(2)))
    if version < MIN_OPENSSH:
        return (
            f"OpenSSH {version[0]}.{version[1]} found ({keygen}); signing needs "
            f"ssh-keygen -Y from OpenSSH {MIN_OPENSSH[0]}.{MIN_OPENSSH[1]} or newer"
        )
    return None


def _env(*, agent: bool) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DISPLAY", "SSH_ASKPASS", "SSH_AUTH_SOCK")
    }
    env["SSH_ASKPASS_REQUIRE"] = "never"
    if agent and "SSH_AUTH_SOCK" in os.environ:
        env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
    return env


def _keygen(
    args: list[str], data: bytes, *, agent: bool = False
) -> subprocess.CompletedProcess[bytes]:
    """One ``ssh-keygen`` call: *data* on stdin, never a terminal, a timeout."""
    problem = openssh_problem()
    if problem is not None:
        raise SigningError(problem)
    keygen = _ssh_keygen()
    assert keygen is not None
    try:
        return subprocess.run(
            [keygen, *args],
            input=data,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            env=_env(agent=agent),
            start_new_session=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise SigningError(
            f"ssh-keygen did not finish within {TIMEOUT_SECONDS:.0f} s"
        ) from None
    except OSError as exc:
        raise SigningError(f"ssh-keygen could not run: {exc}") from None


def _stderr(result: subprocess.CompletedProcess[bytes]) -> str:
    return " ".join(result.stderr.decode("utf-8", "replace").split())


def sign_manifest(bundle_dir: Path, key: Path, *, force: bool = False) -> Path:
    """Sign *bundle_dir*'s manifest with *key* and write ``manifest.json.sig``
    (docs/adr/0008 S1-S4). Refuses an invalid bundle, and an existing
    signature unless *force*; replaces it atomically. A ``.pub`` *key*
    signs with the matching key in ``ssh-agent``. Returns the signature's
    path."""
    from agentevalops.bundles.validator import validate_bundle

    if not key.is_file():
        raise SigningError(f"no key at {key}")
    result = validate_bundle(bundle_dir)
    if not result.valid:
        raise SigningError(
            "the bundle is not valid, so it is not signed: " + "; ".join(result.errors)
        )
    signature = bundle_dir / MANIFEST_SIGNATURE_FILENAME
    if signature.exists() and not force:
        raise SigningError(f"{signature} already exists; pass --force to replace it")
    data = (bundle_dir / MANIFEST_FILENAME).read_bytes()
    agent = key.suffix == ".pub"
    signed = _keygen(
        ["-Y", "sign", "-n", SIGNING_NAMESPACE, "-f", str(key)], data, agent=agent
    )
    if signed.returncode != 0 or not signed.stdout.startswith(_ARMOUR):
        reason = _stderr(signed)
        if "passphrase" in reason:
            reason += (
                " -- a key with a passphrase is not prompted for: load it into "
                "ssh-agent and pass its .pub with --key"
            )
        raise SigningError(f"ssh-keygen could not sign with {key}: {reason}")
    partial = signature.with_name(f".{signature.name}.partial")
    partial.write_bytes(signed.stdout)
    os.replace(partial, signature)
    return signature


def verify_manifest(bundle_dir: Path, allowed_signers: Path) -> Signer:
    """Verify *bundle_dir*'s ``manifest.json.sig`` against *allowed_signers*
    in the bundle namespace and name the signer (docs/adr/0008 V1).
    Raises ``SigningError`` with the cause when there is no signature, the
    signer is not listed, or the signature does not verify."""
    if not allowed_signers.is_file():
        raise SigningError(f"no allowed-signers file at {allowed_signers}")
    signature = bundle_dir / MANIFEST_SIGNATURE_FILENAME
    if not signature.is_file():
        raise SigningError(f"there is no {MANIFEST_SIGNATURE_FILENAME} to verify")
    found = _keygen(
        ["-Y", "find-principals", "-f", str(allowed_signers), "-s", str(signature)],
        b"",
    )
    principals = found.stdout.decode("utf-8", "replace").split()
    if found.returncode != 0 or not principals:
        raise SigningError(
            f"the signature's key is not in {allowed_signers}: {_stderr(found)}"
        )
    data = (bundle_dir / MANIFEST_FILENAME).read_bytes()
    reason = ""
    for principal in principals:
        checked = _keygen(
            [
                "-Y",
                "verify",
                "-f",
                str(allowed_signers),
                "-I",
                principal,
                "-n",
                SIGNING_NAMESPACE,
                "-s",
                str(signature),
            ],
            data,
        )
        if checked.returncode == 0:
            said = checked.stdout.decode("utf-8", "replace") + _stderr(checked)
            fingerprint = _FINGERPRINT.search(said)
            return Signer(principal, fingerprint.group(1) if fingerprint else "unknown")
        reason = _stderr(checked)
    raise SigningError(f"the signature does not verify: {reason}")

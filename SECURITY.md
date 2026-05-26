# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | Yes (once tagged) |
| 0.0.x dev | No |

---

## Scope

AgentEvalOps is a **local-first evaluation framework** with no network
connections in its runtime code. It does not:

- connect to any remote API or cloud service at runtime
- store credentials or secrets
- execute untrusted code from bundles or configs
- provide multi-tenant isolation

There is no production deployment to secure at this stage.

---

## Reporting a vulnerability

If you discover a security issue (for example, path traversal in bundle
reading, unsafe YAML loading, or unsafe deserialization), please:

1. **Do not open a public issue.**
2. Open a [GitHub Security Advisory](https://docs.github.com/en/code-security/security-advisories)
   on this repository (Security → Advisories → New draft advisory).
3. Describe the issue, steps to reproduce, and potential impact.

We will respond and publish a fix before public disclosure.

---

## No production guarantees

This project is early-stage and does not currently provide security
guarantees suitable for production use. SHA-256 checksums in `manifest.json`
detect accidental corruption and naive tampering — they are **not**
cryptographic signing and provide no remote attestation or governance
guarantee.

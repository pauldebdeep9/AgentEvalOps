# Release checklist

Follow these steps before tagging a release.

> **Next target:** `v0.1.0`

---

## 1. Verify working tree

```bash
git status
git diff
```

Ensure no uncommitted changes or unintentionally staged files.

---

## 2. Bump version

Edit `src/agentevalops/__init__.py`:

```python
__version__ = "X.Y.Z"
```

Edit `pyproject.toml`:

```toml
version = "X.Y.Z"
```

Both values must match.

> For v0.1.0: version is already `0.1.0` in both files.

---

## 3. Update CHANGELOG.md

- Rename `## [X.Y.Z] — Unreleased` to `## [X.Y.Z] — YYYY-MM-DD`.
- Add a new empty `## Unreleased` section above it.
- Summarise what changed in plain language.

---

## 4. Run pre-commit

```bash
pre-commit run --all-files
```

Fix any auto-applied changes, then re-run until clean.

---

## 5. Run quality gates

```bash
make check
```

This runs ruff, mypy, and pytest. All must pass.

---

## 6. Run smoke test

```bash
make smoke
```

All three CLI commands must exit 0: `run`, `validate-bundle`, `replay`.

---

## 7. Build and validate the package

```bash
make build
python -m twine check dist/*
```

Both source distribution (`.tar.gz`) and wheel (`.whl`) must be produced
without warnings.

---

## 8. Inspect before committing

- [ ] `README.md` current capabilities and quickstart are accurate
- [ ] `CHANGELOG.md` date is set
- [ ] `LICENSE` present
- [ ] Version in `pyproject.toml` and `__init__.py` match
- [ ] No `dist/`, `build/`, `runs/`, or `.coverage` files staged

---

## 9. Commit and tag

```bash
git add src/agentevalops/__init__.py pyproject.toml CHANGELOG.md
git commit -m "chore: release vX.Y.Z"
git tag vX.Y.Z
git push origin master --tags
```

---

## 10. Publish (when explicitly configured)

PyPI publishing is **not yet configured**. Do not run `twine upload` until
a trusted-publishing or API-token workflow is in place.

When publishing is configured, the intended command will be:

```bash
python -m twine upload dist/*
```

---

## 11. Post-release

- Open a new PR that starts the next `## Unreleased` section.
- Remove old `dist/` and `build/` outputs locally (`make clean`).

"""Triage: a human walks a bundle's task runs and records a verdict on each
(ROADMAP R38).

The bundle is read, never written: its runs are opened through
``BundleReader`` and the typed loaders, and the reviewer's verdicts go to
a sidecar log beside it, ``<bundle>.triage.jsonl`` -- outside the format's
file list, so validation, the gate and rescore never see it. Grouping the
notes into failure modes stays a human step; nothing here calls a model.

- ``store``: the sidecar -- its path, appending an annotation, loading the
  last one per task run.
- ``frame``: which runs are walked, and in what order -- every failed run,
  a seeded share of the passing ones, shuffled together.
"""

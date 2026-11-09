"""Draw the README's two diagrams as SVG with fixed, uniform geometry.

No dependency, no font measuring, no layout engine: every box has the same
size, lines are broken by hand in the data below, and the output bytes are
deterministic, so a test can regenerate and compare.

    python scripts/render_diagrams.py [OUT_DIR]
        -> structure-{light,dark}.svg, run-{light,dark}.svg
           (OUT_DIR defaults to docs/diagrams)

This file is the diagrams' source of truth: edit the data below, run it,
and commit the SVGs; tests/test_diagrams.py fails when the committed files
are not a fresh render. Before drawing, ``check`` refuses data that names a
package that does not exist or leaves one out, a line too long for its box,
or a claim word (DiagramError).
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "src" / "agentevalops"
OUT_DIR = ROOT / "docs" / "diagrams"

FONT = '"Inter", -apple-system, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif'


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    text: str
    muted: str
    box_fill: str
    box_stroke: str
    arrow: str
    # tier accents: (accent, band fill, band stroke)
    interface: tuple[str, str, str]
    engine: tuple[str, str, str]
    shared: tuple[str, str, str]
    core: tuple[str, str, str]
    # run-diagram classes
    built: str
    verdict: str
    planned: str
    loop_fill: str
    loop_stroke: str


LIGHT = Palette(
    name="light",
    bg="#ffffff",
    text="#1f2328",
    muted="#59636e",
    box_fill="#ffffff",
    box_stroke="#d0d7de",
    arrow="#6e7781",
    interface=("#0969da", "#f3f7fc", "#c9d6e6"),
    engine=("#1a7f37", "#f2f8f4", "#c2dccb"),
    shared=("#9a6700", "#fcf8ec", "#e4d7b3"),
    core=("#8250df", "#f7f4fc", "#d8ccf0"),
    built="#2da44e",
    verdict="#0969da",
    planned="#8c959f",
    loop_fill="#f6f8fa",
    loop_stroke="#d0d7de",
)

DARK = Palette(
    name="dark",
    bg="#0d1117",
    text="#e6edf3",
    muted="#9198a1",
    box_fill="#161b22",
    box_stroke="#3d444d",
    arrow="#8b949e",
    interface=("#4493f8", "#111a27", "#1f3b5f"),
    engine=("#3fb950", "#0f1a14", "#1e4a2b"),
    shared=("#d29922", "#1c1810", "#5a4612"),
    core=("#ab7df8", "#171226", "#3b2a5c"),
    built="#3fb950",
    verdict="#4493f8",
    planned="#6e7681",
    loop_fill="#10151c",
    loop_stroke="#30363d",
)

# ---------------------------------------------------------------------------
# Structure diagram data: tiers, top to bottom; each box is (name, lines)
# ---------------------------------------------------------------------------

STRUCTURE = [
    ("interface", "Interface", [
        ("cli", ["run, gate, report, sign,", "validate-bundle, replay,", "rescore, version, doctor"]),
    ]),
    ("engine", "Engine and adapters", [
        ("orchestration", ["the run loop: preflight,", "k repetitions, scoring;", "verdicts rescore reuses"]),
        ("registry", ["names to components;", "entry-point discovery"]),
        ("config", ["run config and", "system.yaml loaders"]),
        ("agents", ["runners: mock,", "langgraph, openai,", "azure-openai"]),
        ("benchmarks", ["toy; agentic-baseline", "over a domain pack"]),
        ("domain_packs", ["seed: tools, cases,", "scenarios, thresholds"]),
        ("evaluators", ["deterministic, tool_use,", "state_based, policy"]),
        ("scorers", ["simple counts;", "pass^k"]),
        ("policy", ["run-level checks:", "cost, trace ceiling,", "tool lists"]),
        ("stores", ["trace stores:", "memory, local jsonl"]),
        ("bundles", ["write, render, manifest,", "read, typed loaders,", "sign, validate"]),
        ("plans", ["declaration to plan;", "control coverage;", "the fail-closed gate"]),
        ("replay", ["cross-file checks;", "rebuild; rescore"]),
        ("reports", ["report.md;", "evidence report"]),
        ("triage", ["a reviewer's verdicts", "beside a bundle:", "frame, sidecar"]),
        ("observability", ["OpenTelemetry scaffold;", "emits nothing yet"]),
    ]),
    ("shared", "Shared services", [
        ("environments", ["in-process tools:", "approver gate, faults,", "injections, call records"]),
        ("catalogue", ["control sources and ids", "as data: identifiers,", "never their text"]),
    ]),
    ("core", "Core", [
        ("core", ["protocols, schemas, types", "errors, pii, identifiers,", "scripts, the derived seed"]),
    ]),
]

# ---------------------------------------------------------------------------
# Run diagram data: (title, detail lines) per box, and each box's kind
# ---------------------------------------------------------------------------

#: Above the loop, top to bottom (260-px boxes). The registry resolves the
#: benchmark and its pack before the plan, which records the pack's hashes;
#: the preflight runs once per scenario, before any agent run.
RUN_BEFORE = [
    ("run config + system.yaml", []),
    ("registry resolves", ["benchmark and pack, evaluators,", "policy checker, runner"]),
    ("test plan written", ["its sha256 registered", "before the first task"]),
    ("known-good preflight", ["each scenario proven solvable", "before any agent runs"]),
]
#: The loop band's label, and its steps down to the runner (260-px boxes).
LOOP_LABEL = "FOR EACH SCENARIO, k REPETITIONS"
RUN_LOOP = [
    ("environment built", ["tools, approvals, faults,", "injections, perturbed text"]),
    ("runner loop", ["scripted (mock),", "or model-backed"]),
]
#: Beside the runner: planned (200-px box).
RUN_SIDE = ("live run on a real model", ["cost reconciled (R30)"])
#: Under the runner, side by side (200-px boxes).
RUN_PAIR = [
    ("every tool call", ["recorded by the harness"]),
    ("end-state snapshot", ["taken before any", "evaluator runs"]),
]
#: The loop's last step (260-px box).
RUN_EVALUATE = ("evaluators", ["deterministic, tool_use,", "state_based, policy"])
#: Below the loop (260-px boxes).
RUN_AFTER = [
    ("pass^k scorer + policy checker", ["per scenario and per category;", "the run-level policy verdict"]),
    ("bundle, format 0.6", ["14 files; manifest with", "a checksum of each"]),
]
#: What reads the bundle (200-px boxes): (title, detail, kind).
RUN_FAN = [
    ("validate-bundle", ["structure, checksums,", "cross-file checks"], "verdict"),
    ("gate", ["fails closed: exit 0 pass,", "1 fail, 2 cannot judge"], "verdict"),
    ("evidence report", ["+ controls.json;", "per control, no composite"], "built"),
    ("sign + verify; rescore", ["MATCH, DIFFER,", "CODE_CHANGED"], "verdict"),
]

# ---------------------------------------------------------------------------
# The checks run before drawing
# ---------------------------------------------------------------------------

#: The most characters a line may have, by box width in px: the fixed
#: geometry measures no text, so a longer line would overflow its box.
LINE_LIMITS = {156: 25, 200: 30, 260: 40}

#: Wording no label may use: no claim of compliance or certification, and
#: no framework named (ADR-0005 D10, ADR-0007 D7).
FORBIDDEN = re.compile(r"\b(compliant|certified)\b|ai[\s_-]*verify", re.IGNORECASE)


class DiagramError(ValueError):
    """The diagram data breaks a rule; the message lists every problem."""


def packages_on_disk(package_dir: Path = PACKAGE_DIR) -> set[str]:
    """The package's top-level packages (a directory with __init__.py) and
    top-level modules (cli, registry): what the structure diagram names."""
    packages = {
        d.name
        for d in package_dir.iterdir()
        if d.is_dir() and (d / "__init__.py").exists()
    }
    modules = {f.stem for f in package_dir.glob("*.py") if f.name != "__init__.py"}
    return packages | modules


def package_problems(package_dir: Path = PACKAGE_DIR) -> list[str]:
    """The structure diagram names every package and top-level module under
    *package_dir*, once each, and nothing else."""
    names = [name for _, _, boxes in STRUCTURE for name, _ in boxes]
    expected = packages_on_disk(package_dir)
    problems = [f"{n} is named more than once" for n in sorted(set(names)) if names.count(n) > 1]
    problems += [f"{n} is on disk but not in the diagram" for n in sorted(expected - set(names))]
    problems += [f"{n} is in the diagram but not on disk" for n in sorted(set(names) - expected)]
    return problems


def boxes() -> list[tuple[str, int, list[str]]]:
    """Every box as (title, width in px, its lines, title first)."""
    out = [(name, BOX_W, [name, *lines]) for _, _, group in STRUCTURE for name, lines in group]
    for title, detail in [*RUN_BEFORE, *RUN_LOOP, RUN_EVALUATE, *RUN_AFTER]:
        out.append((title, RB_W, [title, *detail]))
    for title, detail in [RUN_SIDE, *RUN_PAIR, *((t, d) for t, d, _ in RUN_FAN)]:
        out.append((title, FAN_W, [title, *detail]))
    return out


def length_problems() -> list[str]:
    return [
        f"{title!r}: {line!r} is {len(line)} characters; a {width}-px box takes "
        f"{LINE_LIMITS[width]}"
        for title, width, lines in boxes()
        for line in lines
        if len(line) > LINE_LIMITS[width]
    ]


def labels() -> list[str]:
    """Every piece of text the diagrams draw."""
    titles = [title for _, title, _ in STRUCTURE] + [LOOP_LABEL]
    return titles + [line for _, _, lines in boxes() for line in lines]


def wording_problems() -> list[str]:
    return [f"{label!r} uses {m.group(0)!r}" for label in labels() if (m := FORBIDDEN.search(label))]


def check(package_dir: Path = PACKAGE_DIR) -> None:
    """Raise DiagramError listing every rule the data breaks."""
    problems = package_problems(package_dir) + length_problems() + wording_problems()
    if problems:
        raise DiagramError("the diagram data breaks a rule:\n  " + "\n  ".join(problems))


# ---------------------------------------------------------------------------
# SVG helpers
# ---------------------------------------------------------------------------


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x: float, y: float, s: str, *, size: float, fill: str, weight: int = 400,
         anchor: str = "middle", spacing: str | None = None, mono: bool = False) -> str:
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    family = '"SFMono-Regular", Menlo, Consolas, monospace' if mono else FONT
    return (f'<text x="{x:g}" y="{y:g}" font-family=\'{family}\' font-size="{size:g}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{extra}>{esc(s)}</text>')


def rect(x: float, y: float, w: float, h: float, *, fill: str, stroke: str, rx: float = 6,
         dash: str | None = None, width: float = 1) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{rx:g}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{width:g}"{d}/>')


def path(d: str, *, stroke: str, dash: str | None = None, arrow: bool = True) -> str:
    dd = f' stroke-dasharray="{dash}"' if dash else ""
    m = ' marker-end="url(#arrow)"' if arrow else ""
    return f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="1.4"{dd}{m}/>'


def svg(width: float, height: float, body: list[str], p: Palette) -> str:
    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}" height="{height:g}" '
        f'viewBox="0 0 {width:g} {height:g}" role="img">',
        "<defs>",
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{p.arrow}"/></marker>',
        "</defs>",
        f'<rect width="100%" height="100%" fill="{p.bg}"/>',
    ]
    return "\n".join(head + body + ["</svg>"]) + "\n"


# ---------------------------------------------------------------------------
# Structure diagram
# ---------------------------------------------------------------------------

S_W = 900
S_MARGIN = 20
BAND_W = S_W - 2 * S_MARGIN     # 860
BAND_PAD = 16
BOX_W, BOX_H = 156, 76
GAP = 12
HEAD_H = 34                     # band heading area
BAND_GAP = 36
COLS = 5


def package_box(x: float, y: float, name: str, lines: list[str], accent: str, p: Palette) -> list[str]:
    out = [rect(x, y, BOX_W, BOX_H, fill=p.box_fill, stroke=p.box_stroke)]
    out.append(text(x + 11, y + 22, name, size=13, fill=accent, weight=600, anchor="start", mono=True))
    for i, line in enumerate(lines):
        out.append(text(x + 11, y + 40 + 14 * i, line, size=11, fill=p.muted, anchor="start"))
    return out


def render_structure(p: Palette) -> str:
    body: list[str] = []
    y = S_MARGIN
    arrows_from: list[float] = []
    for key, title, boxes in STRUCTURE:
        accent, band_fill, band_stroke = getattr(p, key)
        rows = (len(boxes) + COLS - 1) // COLS
        band_h = HEAD_H + rows * BOX_H + (rows - 1) * GAP + BAND_PAD
        body.append(rect(S_MARGIN, y, BAND_W, band_h, fill=band_fill, stroke=band_stroke, rx=10))
        body.append(text(S_MARGIN + BAND_PAD, y + 22, title.upper(), size=11, fill=accent,
                         weight=600, anchor="start", spacing="0.08em"))
        for i, (name, lines) in enumerate(boxes):
            row, col = divmod(i, COLS)
            n_in_row = min(COLS, len(boxes) - row * COLS)
            row_w = n_in_row * BOX_W + (n_in_row - 1) * GAP
            x0 = S_MARGIN + (BAND_W - row_w) / 2
            bx = x0 + col * (BOX_W + GAP)
            by = y + HEAD_H + row * (BOX_H + GAP)
            body += package_box(bx, by, name, lines, accent, p)
        arrows_from.append(y + band_h)
        y += band_h + BAND_GAP
    height = y - BAND_GAP + S_MARGIN
    cx = S_W / 2
    for yb in arrows_from[:-1]:
        body.append(path(f"M {cx:g} {yb + 2:g} V {yb + BAND_GAP - 3:g}", stroke=p.arrow))
    return svg(S_W, height, body, p)


# ---------------------------------------------------------------------------
# Run diagram
# ---------------------------------------------------------------------------

R_W = 880
CX = R_W / 2
RB_W, RB_H = 260, 68
FAN_W = 200
VGAP = 26


def step_box(x: float, y: float, w: float, title: str, detail: list[str], kind: str, p: Palette) -> list[str]:
    color = {"built": p.built, "verdict": p.verdict, "planned": p.planned}[kind]
    dash = "5 4" if kind == "planned" else None
    out = [rect(x, y, w, RB_H, fill=p.box_fill, stroke=p.box_stroke, dash=dash)]
    # accent bar on the left, clipped to the rounded box by drawing a rounded rect
    out.append(f'<rect x="{x:g}" y="{y + 6:g}" width="4" height="{RB_H - 12:g}" rx="2" fill="{color}"/>')
    tx = x + w / 2 + 2
    if detail:
        out.append(text(tx, y + 27, title, size=13, fill=p.text, weight=600))
        for i, line in enumerate(detail):
            out.append(text(tx, y + 44 + 14 * i, line, size=11.5, fill=p.muted))
    else:
        out.append(text(tx, y + RB_H / 2 + 5, title, size=13, fill=p.text, weight=600))
    return out


def render_run(p: Palette) -> str:
    body: list[str] = []
    bx = CX - RB_W / 2
    y = 20

    def column(title: str, detail: list[str], kind: str = "built") -> float:
        nonlocal y
        body.extend(step_box(bx, y, RB_W, title, detail, kind, p))
        top = y
        y += RB_H + VGAP
        return top

    def varrow(y_from: float, y_to: float, x: float = CX) -> None:
        body.append(path(f"M {x:g} {y_from + 2:g} V {y_to - 3:g}", stroke=p.arrow))

    tops = [column(title, detail) for title, detail in RUN_BEFORE]
    for above, below in zip(tops, tops[1:]):
        varrow(above + RB_H, below)

    # loop band
    loop_top = y
    band_x, band_w = 16, R_W - 32
    y += 36
    loop = [column(title, detail) for title, detail in RUN_LOOP]
    f = loop[-1]
    g_y = y
    # records and snapshot: a symmetric pair under the runner
    pair_gap = 24
    gx = CX - pair_gap / 2 - FAN_W
    hx = CX + pair_gap / 2
    for x, (title, detail) in zip((gx, hx), RUN_PAIR):
        body.extend(step_box(x, g_y, FAN_W, title, detail, "built", p))
    y += RB_H + VGAP
    i = column(*RUN_EVALUATE)
    loop_bottom = y - VGAP + 16
    # planned side box, aligned with the runner loop
    sx = bx + RB_W + 24
    body.extend(step_box(sx, f, FAN_W, RUN_SIDE[0], RUN_SIDE[1], "planned", p))
    body.append(path(f"M {bx + RB_W + 2:g} {f + RB_H / 2:g} H {sx - 3:g}", stroke=p.arrow, dash="5 4"))
    # band behind (insert before the boxes drawn after loop_top)
    band = [
        rect(band_x, loop_top, band_w, loop_bottom - loop_top, fill=p.loop_fill, stroke=p.loop_stroke, rx=10, dash="3 3"),
        text(band_x + 16, loop_top + 22, LOOP_LABEL, size=11, fill=p.muted,
             weight=600, anchor="start", spacing="0.08em"),
    ]
    varrow(tops[-1] + RB_H, loop[0])
    for above, below in zip(loop, loop[1:]):
        varrow(above + RB_H, below)
    # runner -> the pair, and the pair -> evaluators, each through a short bus
    gcx, hcx = gx + FAN_W / 2, hx + FAN_W / 2
    bus1 = f + RB_H + VGAP / 2
    body.append(path(f"M {CX:g} {f + RB_H + 2:g} V {bus1:g}", stroke=p.arrow, arrow=False))
    body.append(path(f"M {gcx:g} {bus1:g} H {hcx:g}", stroke=p.arrow, arrow=False))
    for x in (gcx, hcx):
        body.append(path(f"M {x:g} {bus1:g} V {g_y - 3:g}", stroke=p.arrow))
    bus2 = g_y + RB_H + VGAP / 2
    for x in (gcx, hcx):
        body.append(path(f"M {x:g} {g_y + RB_H + 2:g} V {bus2:g}", stroke=p.arrow, arrow=False))
    body.append(path(f"M {gcx:g} {bus2:g} H {hcx:g}", stroke=p.arrow, arrow=False))
    body.append(path(f"M {CX:g} {bus2:g} V {i - 3:g}", stroke=p.arrow))

    y = loop_bottom + VGAP
    after = [column(title, detail) for title, detail in RUN_AFTER]
    varrow(i + RB_H, after[0])
    for above, below in zip(after, after[1:]):
        varrow(above + RB_H, below)
    k = after[-1]

    # fan-out bus
    fan = RUN_FAN
    fan_gap = (R_W - 32 - 4 * FAN_W) / 3
    fy = y
    bus_y = k + RB_H + 14
    xs = []
    for n, (title, detail, kind) in enumerate(fan):
        fx = 16 + n * (FAN_W + fan_gap)
        xs.append(fx + FAN_W / 2)
        body.extend(step_box(fx, fy, FAN_W, title, detail, kind, p))
    body.append(path(f"M {CX:g} {k + RB_H + 2:g} V {bus_y:g}", stroke=p.arrow, arrow=False))
    body.append(path(f"M {xs[0]:g} {bus_y:g} H {xs[-1]:g}", stroke=p.arrow, arrow=False))
    for x in xs:
        body.append(path(f"M {x:g} {bus_y:g} V {fy - 3:g}", stroke=p.arrow))
    height = fy + RB_H + 20
    # draw the loop band underneath everything: prepend
    body = band + body
    return svg(R_W, height, body, p)


def render() -> dict[str, bytes]:
    """Every diagram file, by name, as bytes; after the checks pass."""
    check()
    files = {}
    for p in (LIGHT, DARK):
        files[f"structure-{p.name}.svg"] = render_structure(p).encode("utf-8")
        files[f"run-{p.name}.svg"] = render_run(p).encode("utf-8")
    return files


def main(out: Path = OUT_DIR) -> None:
    files = render()
    out.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out / name).write_bytes(data)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DIR)

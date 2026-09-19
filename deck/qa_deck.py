"""Geometry QA for the generated deck: does the text fit its boxes?

Text overflow is the defect that actually reaches an audience, and the usual
way to catch it is to render every slide and look. LibreOffice is not always
available, so this does the arithmetic instead: resolve each placeholder's
effective size and font size through the slide -> layout -> master chain, then
estimate how many lines the text needs against how many the box holds.

Crucially it calibrates against the template. Many of the template's own
placeholders are tighter than their default text - a 14pt slide-number box
holding 18pt digits, say - so an absolute check reports dozens of "overflows"
that ship fine in the template itself. Instead, each placeholder is scored
against the same placeholder's original content: only text that needs *more*
room than the template's own is reported. That is the same discipline
validate.py applies with --original.

It is an estimate, not a renderer. Treat a finding as "open this slide and
look", not as proof.

    python deck/qa_deck.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu

HERE = Path(__file__).resolve().parent
DECK = HERE / "from-chaos-to-symphony.pptx"
TEMPLATE = HERE / "BalticSummitTemplate.pptx"

EMU_PER_POINT = 12700
#: Average glyph advance as a fraction of the point size. Garet is a geometric
#: sans; 0.50 is a fair mean for mixed-case text and errs slightly wide, which
#: is the direction you want an overflow check to err in.
CHAR_WIDTH_RATIO = 0.50
LINE_HEIGHT_RATIO = 1.22
#: PowerPoint's default text inset is 0.1" left/right and 0.05" top/bottom;
#: in points that is 7.2 horizontally and 3.6 vertically, per side.
INSET_X = 7.2
INSET_Y = 3.6

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def placeholder_size(shape, slide) -> tuple[int | None, int | None]:
    """Effective width and height, falling back to the layout's placeholder."""
    width, height = shape.width, shape.height
    if width and height:
        return width, height
    try:
        idx = shape.placeholder_format.idx
        for candidate in slide.slide_layout.placeholders:
            if candidate.placeholder_format.idx == idx:
                return candidate.width or width, candidate.height or height
    except (AttributeError, ValueError, KeyError):
        pass
    return width, height


def font_points(shape, slide) -> float:
    """Effective font size in points for the shape's first run."""
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            if run.font.size is not None:
                return run.font.size.pt
    # Fall back to the layout placeholder's level-1 default.
    try:
        idx = shape.placeholder_format.idx
        for candidate in slide.slide_layout.placeholders:
            if candidate.placeholder_format.idx != idx:
                continue
            node = candidate.text_frame._txBody.find(f"{A}lstStyle/{A}lvl1pPr/{A}defRPr")
            if node is not None and node.get("sz"):
                return int(node.get("sz")) / 100
    except (AttributeError, ValueError, KeyError):
        pass
    return 18.0


def demand(shape, slide) -> tuple[float, float, int, float] | None:
    """How much vertical room this shape's text needs, and how much it has.

    Returns (needed_pt, available_pt, lines, font_pt), or None when the shape
    has no text or no resolvable geometry.
    """
    if not shape.has_text_frame or not shape.text_frame.text.strip():
        return None
    width, height = placeholder_size(shape, slide)
    if not width or not height:
        return None

    size = font_points(shape, slide)
    usable_width = Emu(width).pt - INSET_X * 2
    usable_height = Emu(height).pt - INSET_Y * 2
    if usable_width <= 0 or usable_height <= 0:
        return None

    chars_per_line = max(1, int(usable_width / (size * CHAR_WIDTH_RATIO)))
    lines = sum(max(1, math.ceil(len(line) / chars_per_line))
                for line in shape.text_frame.text.split("\n"))
    return lines * size * LINE_HEIGHT_RATIO, usable_height, lines, size


def baseline(template_path: Path) -> dict[tuple[str, int], float]:
    """Line counts the template's own content needs, keyed by (layout, idx).

    This is the calibration: a placeholder the template already fills to 140%
    is a placeholder that renders fine at 140%, so only exceeding *that* is a
    finding.
    """
    if not template_path.exists():
        return {}
    deck = Presentation(str(template_path))
    worst: dict[tuple[str, int], float] = {}
    for slide in deck.slides:
        layout = slide.slide_layout.name
        for shape in slide.shapes:
            measured = demand(shape, slide)
            if measured is None:
                continue
            try:
                idx = shape.placeholder_format.idx
            except (AttributeError, ValueError):
                continue
            key = (layout, idx)
            worst[key] = max(worst.get(key, 0.0), measured[2])
    return worst


def check(deck_path: Path, template_path: Path = TEMPLATE) -> int:
    deck = Presentation(str(deck_path))
    allowed = baseline(template_path)
    problems = 0
    print(f"Geometry QA: {deck_path.name}  ({len(deck.slides)} slides)")
    print(f"Calibrated against: {template_path.name}\n" if allowed
          else "No template baseline; using absolute limits only.\n")

    for number, slide in enumerate(deck.slides, start=1):
        layout = slide.slide_layout.name
        for shape in slide.shapes:
            measured = demand(shape, slide)
            if measured is None:
                continue
            needed, available, lines, size = measured
            try:
                idx = shape.placeholder_format.idx
            except (AttributeError, ValueError):
                idx = None

            # Lines the same placeholder already carries in the template.
            tolerated = allowed.get((layout, idx), 0.0) if idx is not None else 0.0
            capacity = max(1.0, available / (size * LINE_HEIGHT_RATIO))
            budget = max(capacity, tolerated)

            label = shape.text_frame.text.strip().split("\n")[0][:44]
            if lines > budget:
                problems += 1
                print(f"  [OVERFLOW] slide {number:>2}  {label!r}")
                print(f"             {lines} lines at {size:.0f}pt; box holds ~{capacity:.0f}"
                      + (f", template used {tolerated:.0f}" if tolerated else ""))
            elif lines > budget * 0.9 and budget >= 3:
                print(f"  [TIGHT]    slide {number:>2}  {label!r}  ({lines}/{budget:.0f} lines)")

    print(f"\n{problems} likely overflow(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(check(Path(sys.argv[1]) if len(sys.argv) > 1 else DECK))

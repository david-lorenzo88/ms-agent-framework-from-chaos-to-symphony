"""Build the session deck from the Baltic Summit template.

The template is the source of truth for every visual decision — colours, the
Garet face, the page gradient, the placeholder geometry. This script only
clones its layouts and pours text in, so the deck stays on-brand without a
single hard-coded style.

    python deck/build_deck.py

Produces deck/from-chaos-to-symphony.pptx.
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from content import NOTES, SLIDES, SPEAKERS, TITLE  # noqa: E402

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "BalticSummitTemplate.pptx"
WORK = HERE / "build"
OUT = HERE / "from-chaos-to-symphony.pptx"

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

#: Which template slide supplies each kind of layout.
SOURCE = {"section": "slide4.xml", "two": "slide6.xml", "four": "slide7.xml", "one": "slide5.xml"}

#: Placeholder idx values for the column layouts, as (header, body) pairs.
TWO_COLS = [("15", "13"), ("16", "14")]
FOUR_COLS = [("15", "13"), ("16", "14"), ("19", "17"), ("20", "18")]
ONE_BODY = "13"


# --------------------------------------------------------------------------
# XML helpers
# --------------------------------------------------------------------------


def load(path: Path) -> etree._ElementTree:
    return etree.parse(str(path))


def save(tree: etree._ElementTree, path: Path) -> None:
    tree.write(str(path), xml_declaration=True, encoding="UTF-8", standalone=True)


def shapes(tree: etree._ElementTree):
    """Every <p:sp> in the slide, including ones nested in groups."""
    return tree.getroot().iter(P + "sp")


def find_placeholder(tree, *, idx: str | None = None, ph_type: str | None = None):
    """The shape whose placeholder matches, or None."""
    for sp in shapes(tree):
        ph = sp.find(f".//{P}nvSpPr/{P}nvPr/{P}ph")
        if ph is None:
            continue
        if idx is not None and ph.get("idx") == idx:
            return sp
        if ph_type is not None and ph.get("type") == ph_type:
            return sp
    return None


def find_by_text(tree, needle: str):
    """The shape containing this text. Used for the title slide's speaker boxes."""
    for sp in shapes(tree):
        text = "".join(t.text or "" for t in sp.iter(A + "t"))
        if needle in text:
            return sp
    return None


def set_text(sp, lines: list[str]) -> None:
    """Replace a shape's paragraphs with ``lines``, keeping the template's styling.

    The first existing paragraph is the model: its <a:pPr> carries indent,
    bullet and spacing, and its first run's <a:rPr> carries the face, size and
    colour. Cloning those per line is what keeps the deck on-brand — assigning
    plain text would collapse every run to an unstyled default.
    """
    if sp is None:
        return
    body = sp.find(f"{P}txBody")
    if body is None:
        return

    paragraphs = body.findall(f"{A}p")
    model = paragraphs[0] if paragraphs else None
    model_ppr = model.find(f"{A}pPr") if model is not None else None
    model_run = model.find(f"{A}r") if model is not None else None
    model_rpr = model_run.find(f"{A}rPr") if model_run is not None else None
    end_rpr = model.find(f"{A}endParaRPr") if model is not None else None

    for paragraph in paragraphs:
        body.remove(paragraph)

    for line in lines:
        para = etree.SubElement(body, A + "p")
        if model_ppr is not None:
            para.append(_clone(model_ppr))
        if not line:
            # An intentional blank line: keep the paragraph, give it no run.
            if end_rpr is not None:
                para.append(_clone(end_rpr))
            continue
        run = etree.SubElement(para, A + "r")
        if model_rpr is not None:
            run.append(_clone(model_rpr))
        text = etree.SubElement(run, A + "t")
        text.text = line
        if line != line.strip():
            text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def _clone(node):
    return etree.fromstring(etree.tostring(node))


def remove_shape(sp) -> None:
    """Delete a shape (or the group that contains it) from the tree."""
    if sp is not None and sp.getparent() is not None:
        sp.getparent().remove(sp)


def shift_group(group, dy: int) -> None:
    """Move a group shape down by ``dy`` EMU, offsets and child offsets alike."""
    xfrm = group.find(f"{P}grpSpPr/{A}xfrm")
    if xfrm is None:
        return
    for tag in (f"{A}off", f"{A}chOff"):
        node = xfrm.find(tag)
        if node is not None:
            node.set("y", str(int(node.get("y")) + dy))


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


def unpack() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    with zipfile.ZipFile(TEMPLATE) as archive:
        archive.extractall(WORK)


CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
SLIDE_CT = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
SLIDE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
NOTES_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"


def clone_slide(source: str) -> str:
    """Duplicate a slide, doing every piece of package bookkeeping it needs.

    Four registrations, and a deck is unopenable if any one is missed: the new
    part, its relationships, a content-type override, and an entry in
    presentation.xml.rels. The source's notesSlide relationship is dropped, or
    two slides would share one set of speaker notes.
    """
    slides = WORK / "ppt/slides"
    used = {int(m.stem[5:]) for m in slides.glob("slide*.xml") if m.stem[5:].isdigit()}
    number = max(used) + 1
    name = f"slide{number}.xml"

    shutil.copyfile(slides / source, slides / name)

    rels_dir = slides / "_rels"
    source_rels = rels_dir / f"{source}.rels"
    if source_rels.exists():
        rels = load(source_rels)
        for rel in list(rels.getroot()):
            if rel.get("Type") == NOTES_REL:
                rels.getroot().remove(rel)
        save(rels, rels_dir / f"{name}.rels")

    types_path = WORK / "[Content_Types].xml"
    types = load(types_path)
    override = etree.SubElement(types.getroot(), CT + "Override")
    override.set("PartName", f"/ppt/slides/{name}")
    override.set("ContentType", SLIDE_CT)
    save(types, types_path)

    pres_rels_path = WORK / "ppt/_rels/presentation.xml.rels"
    pres_rels = load(pres_rels_path)
    existing = {rel.get("Id") for rel in pres_rels.getroot()}
    index = 1
    while f"rId{index}" in existing:
        index += 1
    relationship = etree.SubElement(pres_rels.getroot(), PR + "Relationship")
    relationship.set("Id", f"rId{index}")
    relationship.set("Type", SLIDE_REL)
    relationship.set("Target", f"slides/{name}")
    save(pres_rels, pres_rels_path)

    return name


def prune_unused(order: list[str]) -> None:
    """Delete slides the final deck does not use, and their registrations.

    The template ships seventeen slides; this deck reuses seven of them as
    layout sources. Left behind, the other ten would appear in the deck.
    """
    keep = set(order)
    slides = WORK / "ppt/slides"
    dropped = [p.name for p in slides.glob("slide*.xml") if p.name not in keep]
    if not dropped:
        return

    for name in dropped:
        (slides / name).unlink()
        rels = slides / "_rels" / f"{name}.rels"
        if rels.exists():
            rels.unlink()

    types_path = WORK / "[Content_Types].xml"
    types = load(types_path)
    for override in list(types.getroot()):
        part = override.get("PartName") or ""
        if Path(part).name in dropped:
            types.getroot().remove(override)
    save(types, types_path)

    pres_rels_path = WORK / "ppt/_rels/presentation.xml.rels"
    pres_rels = load(pres_rels_path)
    for rel in list(pres_rels.getroot()):
        target = rel.get("Target") or ""
        if "slides/" in target and Path(target).name in dropped:
            pres_rels.getroot().remove(rel)
    save(pres_rels, pres_rels_path)


def build_title(name: str) -> None:
    """Title slide: set the title, keep two speakers, re-centre the pair."""
    path = WORK / "ppt/slides" / name
    tree = load(path)

    title = find_placeholder(tree, ph_type="ctrTitle")
    set_text(title, [TITLE])

    # Three speaker bands ship in the template; this session has two. Delete the
    # third group outright (not just its text, or its two rounded rectangles are
    # left floating), then slide the remaining pair down by half a band so the
    # block stays optically centred where three used to sit.
    def band_containing(needle: str):
        """The top-level speaker band whose text holds ``needle``.

        The groups are named "Group 10", "Group 11", "Group 16" - the words
        "Speaker 1..3" live in a text box two levels down - so match on content,
        not on a name that says nothing.
        """
        tree_root = tree.getroot().find(f"{P}cSld/{P}spTree")
        for group in tree_root.findall(f"{P}grpSp"):
            if needle in "".join(t.text or "" for t in group.iter(A + "t")):
                return group
        return None

    remove_shape(band_containing("Speaker 3"))

    half_band = (4251110 - 3429000) // 2
    for needle in ("Speaker 1", "Speaker 2"):
        band = band_containing(needle)
        if band is not None:
            shift_group(band, half_band)

    for placeholder, speaker in zip(("Speaker 1", "Speaker 2"), SPEAKERS, strict=True):
        set_text(find_by_text(tree, placeholder), [speaker])

    save(tree, path)


#: The Agenda layout offers five single-line slots, one per item - not one
#: multi-line body. Five items, therefore, and no more.
AGENDA_SLOTS = ("10", "11", "12", "13", "14")
AGENDA = (
    "One year on \u2014 what actually changed",
    "The five orchestrations, all demoed",
    "Underneath them: the workflow graph",
    "Production: durability and failure",
    "Governance, cost and what to measure",
)


def build_agenda(name: str) -> None:
    path = WORK / "ppt/slides" / name
    tree = load(path)
    set_text(find_placeholder(tree, ph_type="title"), ["AGENDA"])
    for idx, item in zip(AGENDA_SLOTS, AGENDA, strict=True):
        set_text(find_placeholder(tree, idx=idx), [item])
    save(tree, path)


def build_slide(name: str, kind: str, payload: dict) -> None:
    path = WORK / "ppt/slides" / name
    tree = load(path)

    title = find_placeholder(tree, ph_type="title")
    if title is None:
        title = find_placeholder(tree, ph_type="ctrTitle")
    set_text(title, [payload["title"]])

    if kind == "one":
        set_text(find_placeholder(tree, idx=ONE_BODY), payload["body"])
    elif kind in ("two", "four"):
        slots = TWO_COLS if kind == "two" else FOUR_COLS
        # A layout may offer more column slots than a slide fills; the extra
        # ones keep the template's own empty placeholders and render as nothing.
        for (header_idx, body_idx), (header, bullets) in zip(slots, payload["cols"], strict=False):
            set_text(find_placeholder(tree, idx=header_idx), [header])
            set_text(find_placeholder(tree, idx=body_idx), bullets)

    save(tree, path)


def set_order(order: list[str]) -> None:
    """Rewrite <p:sldIdLst> to exactly this sequence of slide files."""
    pres_path = WORK / "ppt/presentation.xml"
    rels_path = WORK / "ppt/_rels/presentation.xml.rels"
    pres = load(pres_path)
    rels = load(rels_path)

    target_to_rid = {}
    for rel in rels.getroot():
        target = rel.get("Target")
        if target and "slides/slide" in target:
            target_to_rid[Path(target).name] = rel.get("Id")

    lst = pres.getroot().find(f"{P}sldIdLst")
    for child in list(lst):
        lst.remove(child)
    for position, slide in enumerate(order, start=256):
        node = etree.SubElement(lst, P + "sldId")
        node.set("id", str(position))
        node.set(R + "id", target_to_rid[slide])
    save(pres, pres_path)


def repack() -> None:
    """Zip the working tree back into a .pptx."""
    if OUT.exists():
        OUT.unlink()
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(WORK.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(WORK).as_posix())


def add_notes() -> None:
    """Speaker notes, added afterwards so the XML work stays formatting-only."""
    from pptx import Presentation

    deck = Presentation(str(OUT))
    placed = set()
    for slide in deck.slides:
        titles = [sh.text_frame.text.strip() for sh in slide.shapes
                  if sh.has_text_frame and sh.text_frame.text.strip()]
        for title, text in NOTES.items():
            if title in titles and title not in placed:
                slide.notes_slide.notes_text_frame.text = text
                placed.add(title)
                break
    missing = set(NOTES) - placed
    if missing:
        print(f"  warning: no slide matched these note titles: {sorted(missing)}")
    deck.save(str(OUT))


def main() -> None:
    if not TEMPLATE.exists():
        raise SystemExit(f"Template missing: {TEMPLATE}")
    unpack()

    # Structural work first, in full: add_slide.py copies a slide verbatim, so
    # duplicating after an edit would clone the edited content.
    order: list[str] = ["slide1.xml", "slide2.xml", "slide3.xml"]
    used: dict[str, bool] = {}
    plan: list[tuple[str, str, dict]] = []

    for kind, payload in SLIDES:
        source = SOURCE[kind]
        if not used.get(source):
            used[source] = True
            name = source            # first use consumes the template's own slide
        else:
            name = clone_slide(source)
        order.append(name)
        plan.append((name, kind, payload))

    prune_unused(order)
    set_order(order)

    # Content second.
    build_title("slide1.xml")
    build_agenda("slide3.xml")
    for name, kind, payload in plan:
        build_slide(name, kind, payload)

    repack()
    add_notes()
    print(f"Built {OUT}  ({len(order)} slides)")


if __name__ == "__main__":
    main()

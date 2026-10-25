import base64
import re

import pymupdf
from fastapi import HTTPException

from .schemas import FieldRef, FillValue, Location

MAX_PAGES = 10
_NUMBERING = re.compile(r"^\s*(\d+\.|\(?\s*[a-z]{1,3}\s*\))\s*", re.I)  # "1.", "(a)", "d)", "(c )"

# shortcut: rotated pages are not handled (boxes would be misplaced), handle page.rotation if real forms need it


def open_pdf(data: bytes) -> pymupdf.Document:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:  # pymupdf raises several types for corrupt files
        raise HTTPException(400, "Could not read this PDF.")
    if doc.needs_pass:
        raise HTTPException(400, "This PDF is password-protected.")
    if not 0 < doc.page_count <= MAX_PAGES:
        raise HTTPException(400, f"PDF must have 1 to {MAX_PAGES} pages (this one has {doc.page_count}).")
    return doc


def to_images(data: bytes, mime: str) -> list[tuple[bytes, str]]:
    if mime != "application/pdf":
        return [(data, mime)]
    # 2x zoom (~144 dpi) keeps small form text legible for the model
    return [(page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png"), "image/png") for page in open_pdf(data)]


def as_pdf(data: bytes, mime: str) -> bytes:
    """Images become a one-page PDF so photos and scans can be filled and downloaded too."""
    if mime == "application/pdf":
        return data
    try:
        return pymupdf.open(stream=data, filetype=mime.split("/")[1]).convert_to_pdf()
    except Exception:  # pymupdf raises several types for corrupt files
        raise HTTPException(400, "Could not read this image.")


def data_urls(images: list[tuple[bytes, str]]) -> list[str]:
    return [f"data:{m};base64,{base64.b64encode(b).decode()}" for b, m in images]


Blank = tuple[pymupdf.Rect, list[pymupdf.Rect]]  # where an answer goes, plus its letter cells if it's a letter-box row


def _rows(squares: list[pymupdf.Rect]) -> list[list[pymupdf.Rect]]:
    """Group squares into runs that sit edge to edge on one line (letter-by-letter boxes)."""
    lines: list[list[pymupdf.Rect]] = []
    for r in sorted(squares, key=lambda r: r.y0):
        if lines and r.y0 - lines[-1][0].y0 < 2:
            lines[-1].append(r)
        else:
            lines.append([r])
    rows: list[list[pymupdf.Rect]] = []
    for line in lines:
        start = len(rows)
        for r in sorted(line, key=lambda r: r.x0):
            prev = rows[-1][-1] if len(rows) > start else None
            if prev is not None and -1 <= r.x0 - prev.x1 < 4 and abs(r.width - prev.width) < 2:
                rows[-1].append(r)
            else:
                rows.append([r])
    return rows


def _shapes(page: pymupdf.Page) -> tuple[list[Blank], list[pymupdf.Rect]]:
    """Places an answer can go (text widgets, lines, underscores, empty boxes, letter-box rows), and tick boxes."""
    blanks: list[Blank] = []
    ticks: list[pymupdf.Rect] = []
    for w in page.widgets():
        if w.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT:
            blanks.append((w.rect, []))
        elif w.field_type in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
            ticks.append(w.rect)
    rects: dict[tuple, pymupdf.Rect] = {}  # forms often draw each box twice (fill + stroke)
    for d in page.get_drawings():
        for item in d["items"]:
            if item[0] == "l" and abs(item[1].y - item[2].y) < 1:
                r = pymupdf.Rect(item[1], item[2]).normalize()
                if r.width > 30:
                    blanks.append((pymupdf.Rect(r.x0, r.y0 - 1, r.x1, r.y1), []))
            elif item[0] == "re":
                r = pymupdf.Rect(item[1]).normalize()
                if r.height < 2 and r.width > 30:  # a line drawn as a thin rectangle
                    blanks.append((pymupdf.Rect(r.x0, r.y0 - 1, r.x1, r.y1), []))
                elif r.height >= 5:
                    rects[tuple(round(v, 1) for v in r)] = r
    blanks += [(pymupdf.Rect(w[:4]), []) for w in page.get_text("words") if set(w[4]) == {"_"}]
    # shortcut: boxes drawn as four separate lines aren't detected, only real rectangles; add if forms need it
    squares = [r for r in rects.values() if 5 <= r.width <= 30 and abs(r.width - r.height) <= 3]
    for row in _rows(squares):
        if len(row) >= 3:
            blanks.append((pymupdf.Rect(row[0].x0, row[0].y0, row[-1].x1, row[-1].y1), row))
        else:
            ticks += row
    boxes = [r for r in rects.values() if r.width >= 30 and 10 <= r.height <= 80 and not page.get_text(clip=r).strip()]
    blanks += [(r, []) for r in boxes]
    return blanks, ticks


def _matches(page: pymupdf.Page, text: str) -> list[pymupdf.Rect]:
    # one match can come back as several rects (one per text span); merge neighbours on the same line
    matches: list[pymupdf.Rect] = []
    for r in page.search_for(text):
        if matches and abs(r.y0 - matches[-1].y0) < 3 and 0 <= r.x0 - matches[-1].x1 < 15:
            matches[-1] = matches[-1] | r
        else:
            matches.append(pymupdf.Rect(r))
    return matches


def _find_label(page: pymupdf.Page, text: str, used: list[pymupdf.Rect]) -> pymupdf.Rect | None:
    return next((m for m in _matches(page, text) if not any(m.intersects(u) for u in used)), None)


def _same_line(b: pymupdf.Rect, label: pymupdf.Rect) -> bool:
    return b.y0 <= label.y1 + 6 and b.y1 >= label.y0 - 2


def _answer_area(page: pymupdf.Page, label: pymupdf.Rect, blanks: list[Blank]) -> Blank | None:
    """The blank right of the label, else a box just below it, else the space after it.
    None if the label is a unit suffix like "Per Month"."""
    after = [(b, c) for b, c in blanks if b.x0 >= label.x1 - 5 and _same_line(b, label)]
    if after:
        b, cells = min(after, key=lambda bc: bc[0].x0)
        return (b if cells else pymupdf.Rect(b.x0, min(b.y0, label.y0), b.x1, max(b.y1, label.y1))), cells
    # text right after another field's blank with no blank of its own is a unit like "Per Month", not a field
    if any(0 <= label.x0 - b.x1 < 15 and _same_line(b, label) for b, _ in blanks):
        return None
    # boxes and letter rows can sit under their label; lines are skipped here since they may just underline it
    below = [(b, c) for b, c in blanks if b.height >= 8 and label.y1 - 2 <= b.y0 <= label.y1 + 25 and b.x0 < label.x1 and b.x1 > label.x0]
    if below:
        return min(below, key=lambda bc: bc[0].y0)
    return pymupdf.Rect(label.x1 + 4, label.y0, label.x1 + 200, label.y1) & page.rect, []


def _choice_area(page: pymupdf.Page, label: pymupdf.Rect, options: list[str], ticks: list[pymupdf.Rect]
                 ) -> tuple[dict[str, pymupdf.Rect], pymupdf.Rect]:
    """Each printed option near the label, with the box to tick: the nearest small square beside its text,
    else the spot just left of the text (covers "[ ] Yes" and plain "Yes / No")."""
    choices: dict[str, pymupdf.Rect] = {}
    taken: list[pymupdf.Rect] = []
    group = pymupdf.Rect(label)
    # longest first, so "Male" can't be matched inside an already claimed "Female"
    for opt in sorted(options, key=len, reverse=True):
        hits = [r for r in _matches(page, opt)
                if label.y0 - 2 <= r.y0 <= label.y1 + 60 and not any(r.intersects(t) for t in taken + [label])]
        if not hits:
            continue
        r = min(hits, key=lambda r: abs(r.y0 - label.y0) * 10 + abs(r.x0 - label.x1))
        beside = [s for s in ticks if _same_line(s, r) and (-30 < s.x1 - r.x0 <= 3 or -3 <= s.x0 - r.x1 < 30)]
        tick = (min(beside, key=lambda s: min(abs(s.x1 - r.x0), abs(s.x0 - r.x1))) if beside
                else pymupdf.Rect(r.x0 - 11, r.y0 + 1, r.x0 - 2, r.y1 - 1))
        choices[opt] = tick
        taken.append(r)
        group |= r
        group |= tick
    return choices, group


def overlapping(locations: list[Location]) -> set[int]:
    """Ids whose answer box mostly covers an earlier field's box: two fields can't share one blank."""
    kept: list[Location] = []
    dropped = set()
    for loc in locations:
        r = pymupdf.Rect(loc.box)
        if any(k.page == loc.page and (r & pymupdf.Rect(k.box)).get_area() > 0.6 * min(r.get_area(), pymupdf.Rect(k.box).get_area())
               for k in kept):
            dropped.add(loc.id)
        else:
            kept.append(loc)
    return dropped


def text_locations(data: bytes, fields: list[FieldRef]) -> tuple[dict[int, Location], set[int]]:
    """Exact positions from a PDF's text layer: find the printed label, then the blank next to it.
    Also returns ids that turned out to be unit suffixes rather than fields."""
    pages = [(page, *_shapes(page)) for page in open_pdf(data)]
    used: dict[int, list[pymupdf.Rect]] = {page.number: [] for page, *_ in pages}
    found, suffixes = {}, set()
    for f in fields:
        cleaned = [_NUMBERING.sub("", s).strip(" :*._-\t") for s in (f.label, f.evidence)]
        # the model writes straight quotes; PDFs often have curly ones (Guardian’s)
        terms = [t for t in dict.fromkeys(v for c in cleaned for v in (c, c.replace("'", "’"))) if len(t) >= 3]
        for term in terms:
            hit = next(((p, b, t, r) for p, b, t in pages if (r := _find_label(p, term, used[p.number]))), None)
            if not hit:
                continue
            page, blanks, ticks, label = hit
            used[page.number].append(label)
            w, h = page.rect.width, page.rect.height
            norm = lambda r: [r.x0 / w, r.y0 / h, r.x1 / w, r.y1 / h]  # noqa: E731
            if f.kind == "choice":
                choices, group = _choice_area(page, label, f.options, ticks)
                if choices:  # none found: leave it to the Gemma locator
                    found[f.id] = Location(id=f.id, page=page.number, box=norm(group), label_box=norm(label),
                                           choices={o: norm(r) for o, r in choices.items()})
                break
            area = _answer_area(page, label, blanks)
            if area is None:
                suffixes.add(f.id)
                break
            a, cells = area
            found[f.id] = Location(id=f.id, page=page.number, box=norm(a), label_box=norm(label), cells=[norm(c) for c in cells])
            break
    return found, suffixes


def _tick(page: pymupdf.Page, r: pymupdf.Rect) -> None:
    s = min(r.width, r.height)  # stay square inside a wider fallback spot
    x, y = r.x0 + (r.width - s) / 2, r.y0 + (r.height - s) / 2
    page.draw_polyline([(x + 0.2 * s, y + 0.55 * s), (x + 0.42 * s, y + 0.78 * s), (x + 0.82 * s, y + 0.22 * s)],
                       color=(0, 0, 0), width=max(1.0, s / 8))


def _letters(page: pymupdf.Page, cells: list[pymupdf.Rect], value: str) -> None:
    for ch, c in zip(value, cells):  # extra characters beyond the boxes are dropped; the UI caps input length
        size = min(c.height * 0.7, 14)
        width = pymupdf.get_text_length(ch, fontname="helv", fontsize=size)
        page.insert_text((c.x0 + (c.width - width) / 2, c.y1 - (c.height - size * 0.7) / 2), ch, fontname="helv", fontsize=size)


def fill_pdf(data: bytes, values: list[FillValue]) -> bytes:
    doc = open_pdf(data)
    for i, v in enumerate(values):
        if not v.tick and not v.value.strip():
            continue
        if v.page >= doc.page_count:
            raise HTTPException(400, "Field page does not exist in this PDF.")
        page = doc[v.page]
        w, h = page.rect.width, page.rect.height
        denorm = lambda b: pymupdf.Rect(b[0] * w, b[1] * h, b[2] * w, b[3] * h)  # noqa: E731
        rect = denorm(v.box)
        if v.tick:
            _tick(page, rect)
            continue
        if v.cells:
            _letters(page, [denorm(c) for c in v.cells], v.value)
            continue
        existing = next((x for x in page.widgets() if x.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT
                         and not x.field_name.startswith("docpal_") and x.rect.intersects(rect)), None)
        if existing:
            existing.field_value = v.value
            existing.update()
            continue
        # flat PDF: a text field gives us auto-fit text layout; bake() below turns it into plain page text
        widget = pymupdf.Widget()
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = f"docpal_{i}"
        widget.rect = rect
        widget.border_width = 0
        if rect.height > 25:  # a tall box: normal-size text that wraps, instead of one giant line
            widget.text_fontsize = 11
            widget.field_flags |= pymupdf.PDF_TX_FIELD_IS_MULTILINE
        else:
            widget.text_fontsize = 0  # auto-fit to the line
        widget.field_value = v.value
        page.add_widget(widget)
    # flatten: viewers shade live form fields blue, so answers become normal text that looks finished and prints clean
    doc.bake()
    return doc.tobytes(garbage=3, deflate=True)

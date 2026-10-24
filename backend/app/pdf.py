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


def _blanks(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Places a value can go: text widgets, drawn horizontal lines, underscore runs."""
    out = [w.rect for w in page.widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT]
    for d in page.get_drawings():
        for item in d["items"]:
            if item[0] == "l" and abs(item[1].y - item[2].y) < 1:
                r = pymupdf.Rect(item[1], item[2]).normalize()
            elif item[0] == "re" and item[1].height < 2:
                r = item[1]
            else:
                continue
            if r.width > 30:
                out.append(pymupdf.Rect(r.x0, r.y0 - 1, r.x1, r.y1))
    out += [pymupdf.Rect(w[:4]) for w in page.get_text("words") if set(w[4]) == {"_"}]
    return out


def _find_label(page: pymupdf.Page, text: str, used: list[pymupdf.Rect]) -> pymupdf.Rect | None:
    # one match can come back as several rects (one per text span); merge neighbours on the same line
    matches: list[pymupdf.Rect] = []
    for r in page.search_for(text):
        if matches and abs(r.y0 - matches[-1].y0) < 3 and 0 <= r.x0 - matches[-1].x1 < 15:
            matches[-1] = matches[-1] | r
        else:
            matches.append(pymupdf.Rect(r))
    return next((m for m in matches if not any(m.intersects(u) for u in used)), None)


def _same_line(b: pymupdf.Rect, label: pymupdf.Rect) -> bool:
    return b.y0 <= label.y1 + 6 and b.y1 >= label.y0 - 2


def _answer_area(page: pymupdf.Page, label: pymupdf.Rect, blanks: list[pymupdf.Rect]) -> pymupdf.Rect | None:
    """The blank right of the label; the space after it if there's none; None if the label is a unit suffix."""
    after = [b for b in blanks if b.x0 >= label.x1 - 5 and _same_line(b, label)]
    if after:
        b = min(after, key=lambda b: b.x0)
        return pymupdf.Rect(b.x0, min(b.y0, label.y0), b.x1, max(b.y1, label.y1))
    # text right after another field's blank with no blank of its own is a unit like "Per Month", not a field
    if any(0 <= label.x0 - b.x1 < 15 and _same_line(b, label) for b in blanks):
        return None
    return pymupdf.Rect(label.x1 + 4, label.y0, label.x1 + 200, label.y1) & page.rect


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
    pages = [(page, _blanks(page)) for page in open_pdf(data)]
    used: dict[int, list[pymupdf.Rect]] = {page.number: [] for page, _ in pages}
    found, suffixes = {}, set()
    for f in fields:
        cleaned = [_NUMBERING.sub("", s).strip(" :*._-\t") for s in (f.label, f.evidence)]
        # the model writes straight quotes; PDFs often have curly ones (Guardian’s)
        terms = [t for t in dict.fromkeys(v for c in cleaned for v in (c, c.replace("'", "’"))) if len(t) >= 3]
        for term in terms:
            hit = next(((p, b, r) for p, b in pages if (r := _find_label(p, term, used[p.number]))), None)
            if hit:
                page, blanks, label = hit
                used[page.number].append(label)
                w, h = page.rect.width, page.rect.height
                a = _answer_area(page, label, blanks)
                if a is None:
                    suffixes.add(f.id)
                    break
                found[f.id] = Location(
                    id=f.id,
                    page=page.number,
                    box=[a.x0 / w, a.y0 / h, a.x1 / w, a.y1 / h],
                    label_box=[label.x0 / w, label.y0 / h, label.x1 / w, label.y1 / h],
                )
                break
    return found, suffixes


def fill_pdf(data: bytes, values: list[FillValue]) -> bytes:
    doc = open_pdf(data)
    for i, v in enumerate(values):
        if not v.value.strip():
            continue
        if v.page >= doc.page_count:
            raise HTTPException(400, "Field page does not exist in this PDF.")
        page = doc[v.page]
        w, h = page.rect.width, page.rect.height
        rect = pymupdf.Rect(v.box[0] * w, v.box[1] * h, v.box[2] * w, v.box[3] * h)
        existing = next((x for x in page.widgets() if x.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT
                         and not x.field_name.startswith("docpal_") and x.rect.intersects(rect)), None)
        if existing:
            existing.field_value = v.value
            existing.update()
            continue
        # flat PDF: add a real text field so the download stays editable
        widget = pymupdf.Widget()
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = f"docpal_{i}"
        widget.rect = rect
        widget.border_width = 0
        widget.text_fontsize = 0  # auto-fit to the box
        widget.field_value = v.value
        page.add_widget(widget)
    return doc.tobytes(garbage=3, deflate=True)

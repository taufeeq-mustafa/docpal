import pymupdf
import pytest
from fastapi import HTTPException

from app.pdf import MAX_PAGES, as_pdf, fill_pdf, overlapping, text_locations, to_images
from app.schemas import FieldRef, FillValue, Location


def make_pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page().insert_text((72, 72), "Full Name: ________")
    return doc.tobytes()


def flat_form() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()  # 595 x 842
    page.insert_text((72, 100), "1. Name of Student:")
    page.draw_line((180, 102), (400, 102))
    page.insert_text((72, 140), "Email")
    page.insert_htmlbox(pymupdf.Rect(72, 170, 300, 190), "Guardian’s Name:")  # base14 fonts can't encode ’
    page.insert_text((72, 220), "Pension in Rs.")
    page.draw_line((150, 222), (300, 222))
    page.insert_text((302, 220), "Per Month")  # unit printed after the blank
    return doc.tobytes()


def fillable_form() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Name of Student:")
    w = pymupdf.Widget()
    w.field_type, w.field_name, w.rect = pymupdf.PDF_WIDGET_TYPE_TEXT, "student_name", pymupdf.Rect(180, 88, 400, 104)
    page.add_widget(w)
    return doc.tobytes()


CELL = 14  # letter box size in the boxes form


def boxes_form() -> bytes:
    """Empty boxes, letter-by-letter boxes, checkboxes and bracket choices."""
    doc = pymupdf.open()
    page = doc.new_page()  # 595 x 842
    page.insert_text((72, 100), "Full Name")
    page.draw_rect(pymupdf.Rect(160, 88, 400, 106))  # box to the right
    page.insert_text((72, 140), "Home Address")
    page.draw_rect(pymupdf.Rect(72, 146, 400, 190))  # box underneath
    page.insert_text((72, 220), "Passport No.")
    for i in range(9):
        page.draw_rect(pymupdf.Rect(160 + i * CELL, 208, 160 + (i + 1) * CELL, 208 + CELL))
    page.insert_text((72, 260), "Gender")
    page.draw_rect(pymupdf.Rect(160, 250, 170, 260))
    page.insert_text((174, 259), "Male")
    page.draw_rect(pymupdf.Rect(220, 250, 230, 260))
    page.insert_text((234, 259), "Female")
    page.insert_text((72, 300), "Do you hold a visa?")
    page.insert_text((200, 300), "[ ] Yes   [ ] No")  # no drawn boxes at all
    return doc.tobytes()


def refs(*labels: str) -> list[FieldRef]:
    return [FieldRef(id=i, label=label) for i, label in enumerate(labels)]


def test_pdf_pages_become_pngs():
    images = to_images(make_pdf(2), "application/pdf")
    assert len(images) == 2
    assert all(b.startswith(b"\x89PNG") and m == "image/png" for b, m in images)


def test_image_passes_through():
    assert to_images(b"jpg", "image/jpeg") == [(b"jpg", "image/jpeg")]


@pytest.mark.parametrize("data", [b"not a pdf", make_pdf(MAX_PAGES + 1)])
def test_bad_pdfs_rejected(data):
    with pytest.raises(HTTPException) as e:
        to_images(data, "application/pdf")
    assert e.value.status_code == 400


def test_locate_flat_uses_drawn_line_and_skips_unknown():
    found, suffixes = text_locations(flat_form(), refs("1. Name of Student *", "Email", "Bank account", "Guardian's Name"))
    assert found[0].page == 0 and found[0].box[0] == pytest.approx(180 / 595, abs=0.01)  # starts at the drawn line
    assert found[1].box[0] > found[1].label_box[2]  # no blank: space right after the label
    assert 2 not in found  # left for the Gemma locator
    assert 3 in found  # straight quote matches the PDF's curly one
    assert suffixes == set()


def test_unit_after_a_blank_is_not_a_field():
    found, suffixes = text_locations(flat_form(), refs("Pension in Rs.", "Per Month"))
    assert 0 in found and suffixes == {1}


def test_fields_sharing_a_blank_keep_the_first():
    loc = lambda i, box: Location(id=i, page=0, box=box)
    same, other = [0.2, 0.1, 0.5, 0.12], [0.6, 0.1, 0.9, 0.12]
    assert overlapping([loc(0, same), loc(1, [0.21, 0.1, 0.5, 0.12]), loc(2, other)]) == {1}
    assert overlapping([loc(0, same), Location(id=1, page=1, box=same)]) == set()  # other page


def px(box: list[float]) -> list[float]:
    """Page fractions back to points on the 595 x 842 test page."""
    return [round(box[0] * 595), round(box[1] * 842), round(box[2] * 595), round(box[3] * 842)]


def test_empty_boxes_right_and_below():
    found, _ = text_locations(boxes_form(), refs("Full Name", "Home Address"))
    assert px(found[0].box) == [160, 88, 400, 106] and found[0].cells == []
    assert px(found[1].box) == [72, 146, 400, 190]


def test_letter_boxes_become_cells():
    found, _ = text_locations(boxes_form(), refs("Passport No."))
    assert len(found[0].cells) == 9
    assert px(found[0].cells[0]) == [160, 208, 160 + CELL, 208 + CELL]


def test_choice_ticks_use_squares_and_skip_substring_matches():
    found, _ = text_locations(boxes_form(), [FieldRef(id=0, label="Gender", kind="choice", options=["Male", "Female"])])
    assert px(found[0].choices["Male"]) == [160, 250, 170, 260]  # not the "male" inside "Female"
    assert px(found[0].choices["Female"]) == [220, 250, 230, 260]


def test_choice_without_boxes_ticks_left_of_the_text():
    found, _ = text_locations(boxes_form(), [FieldRef(id=0, label="Do you hold a visa?", kind="choice", options=["Yes", "No"])])
    yes, no = (px(found[0].choices[o]) for o in ("Yes", "No"))
    assert 200 <= yes[0] < yes[2] < no[0]  # each tick spot sits over its "[ ]", in order


def test_fill_writes_one_letter_per_cell_and_ticks():
    pdf = boxes_form()
    passport = text_locations(pdf, refs("Passport No."))[0][0]
    gender = text_locations(pdf, [FieldRef(id=0, label="Gender", kind="choice", options=["Male", "Female"])])[0][0]
    out = fill_pdf(pdf, [FillValue(page=0, box=passport.box, value="AB1234567", cells=passport.cells),
                         FillValue(page=0, box=gender.choices["Female"], tick=True)])
    page = pymupdf.open(stream=out)[0]
    letters = [page.get_text(clip=pymupdf.Rect(px(c))).strip() for c in passport.cells]
    assert letters == list("AB1234567")
    tick = pymupdf.Rect(px(gender.choices["Female"]))
    assert any(d["rect"].intersects(tick) and len(d["items"]) == 2 for d in page.get_drawings())  # the two tick strokes


def test_tall_box_gets_normal_size_wrapped_text():
    box = text_locations(boxes_form(), refs("Home Address"))[0][0].box
    address = "House 12, Street 4, Block B, Gulshan-e-Iqbal, Karachi 75300, Pakistan"
    page = pymupdf.open(stream=fill_pdf(boxes_form(), [FillValue(page=0, box=box, value=address)]))[0]
    sizes = {round(s["size"]) for b in page.get_text("dict", clip=pymupdf.Rect(px(box)))["blocks"] for line in b["lines"] for s in line["spans"]}
    assert sizes == {11}
    assert " ".join(page.get_text(clip=pymupdf.Rect(px(box))).split()) == address  # wrapped, nothing cut off


def test_locate_fillable_uses_widget():
    found, _ = text_locations(fillable_form(), refs("Name of Student"))
    assert found[0].box == pytest.approx([180 / 595, 88 / 842, 400 / 595, 104 / 842], abs=0.002)


def text_in_box(pdf: bytes, box: list[float]) -> str:
    """Filled output must be flat page text (no live form fields) inside the answer box."""
    page = pymupdf.open(stream=pdf)[0]
    assert list(page.widgets()) == []
    w, h = page.rect.width, page.rect.height
    return page.get_text(clip=pymupdf.Rect(box[0] * w, box[1] * h, box[2] * w, box[3] * h) + (-2, -2, 2, 2)).strip()


def test_fill_flat_writes_text_on_the_line():
    box = text_locations(flat_form(), refs("Name of Student"))[0][0].box
    assert text_in_box(fill_pdf(flat_form(), [FillValue(page=0, box=box, value="Ayesha Khan")]), box) == "Ayesha Khan"


def test_fill_fillable_uses_existing_field():
    box = text_locations(fillable_form(), refs("Name of Student"))[0][0].box
    assert text_in_box(fill_pdf(fillable_form(), [FillValue(page=0, box=box, value="Ayesha Khan")]), box) == "Ayesha Khan"


def test_photo_becomes_filled_pdf():
    png = pymupdf.open(stream=make_pdf(1))[0].get_pixmap().tobytes("png")
    box = [0.3, 0.1, 0.6, 0.12]
    assert text_in_box(fill_pdf(as_pdf(png, "image/png"), [FillValue(page=0, box=box, value="Ayesha")]), box) == "Ayesha"

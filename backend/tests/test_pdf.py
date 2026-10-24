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


def test_locate_fillable_uses_widget():
    found, _ = text_locations(fillable_form(), refs("Name of Student"))
    assert found[0].box == pytest.approx([180 / 595, 88 / 842, 400 / 595, 104 / 842], abs=0.002)


def test_fill_flat_adds_editable_field():
    box = text_locations(flat_form(), refs("Name of Student"))[0][0].box
    out = pymupdf.open(stream=fill_pdf(flat_form(), [FillValue(page=0, box=box, value="Ayesha Khan")]))
    assert [w.field_value for w in out[0].widgets()] == ["Ayesha Khan"]


def test_fill_fillable_sets_existing_widget():
    box = text_locations(fillable_form(), refs("Name of Student"))[0][0].box
    out = pymupdf.open(stream=fill_pdf(fillable_form(), [FillValue(page=0, box=box, value="Ayesha Khan")]))
    assert [(w.field_name, w.field_value) for w in out[0].widgets()] == [("student_name", "Ayesha Khan")]


def test_photo_becomes_fillable_pdf():
    png = pymupdf.open(stream=make_pdf(1))[0].get_pixmap().tobytes("png")
    out = pymupdf.open(stream=fill_pdf(as_pdf(png, "image/png"), [FillValue(page=0, box=[0.3, 0.1, 0.6, 0.12], value="Ayesha")]))
    assert out.page_count == 1 and [w.field_value for w in out[0].widgets()] == ["Ayesha"]

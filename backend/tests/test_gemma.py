from app.gemma import parse_boxes, parse_lines

FIELD = '{"type": "field", "label": "Name", "evidence": "Name:", "explanation": "Your name", "required": true, "status": "empty", "you_need": ["ID card"]}'


def test_stream_split_mid_line_with_noise():
    text = "```json\n" + '{"type": "form", "form_title": "Visa Form", "purpose": "Apply"}\n' + FIELD + "\nnot json\n" \
        + '{"type": "field", "label": "missing keys"}\n' + FIELD.replace("Name", "Passport No") + "\n" \
        + '{"type": "summary", "checklist": ["Sign"], "warnings": []}' + "\n```"
    chunks = [text[i:i + 7] for i in range(0, len(text), 7)]  # model streams arbitrary slices
    events = list(parse_lines(chunks))
    assert [e["type"] for e in events] == ["form", "field", "field", "summary"]
    assert [(e["id"], e["label"]) for e in events if e["type"] == "field"] == [(0, "Name"), (1, "Passport No")]


def test_emojis_and_em_dashes_removed():
    line = FIELD.replace("Your name", "Your name — as on your ID ✅ 📄").replace("ID card", "ID card–copy ⚠️")
    field = next(parse_lines([line]))
    assert field["explanation"] == "Your name, as on your ID "
    assert field["you_need"] == ["ID card-copy "]


def test_last_line_without_newline_is_parsed():
    assert [e["type"] for e in parse_lines([FIELD])] == ["field"]


def test_boxes_converted_clamped_and_filtered():
    reply = """Sure! {"boxes": [
        {"id": 0, "page": 0, "box_2d": [100, 200, 120, 600]},
        {"id": 1, "page": 0, "box_2d": [500, 900, 520, 1200]},
        {"id": 2, "page": 5, "box_2d": [1, 1, 2, 2]},
        {"id": 9, "page": 0, "box_2d": [1, 1, 2, 2]},
        {"id": 3, "box_2d": [1, 2]}
    ]}"""
    out = parse_boxes(reply, n_pages=1, ids={0, 1, 2, 3})
    assert out[0].box == [0.2, 0.1, 0.6, 0.12]  # [ymin,xmin,ymax,xmax] -> [x0,y0,x1,y1]
    assert out[1].box[2] == 1.0  # clamped to the page
    assert set(out) == {0, 1}  # bad page, unknown id and malformed box dropped

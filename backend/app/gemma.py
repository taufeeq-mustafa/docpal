"""Two Gemma agents: the explainer streams what each field means, the locator finds where answers go."""
import base64
import json
import os
import re
import urllib.request
from collections.abc import Iterable, Iterator
from functools import cache
from itertools import chain

from .schemas import Field, FieldRef, FormHeader, Location, Summary

EXPLAIN_PROMPT = """You are DocPal, a helper that explains forms of any kind (immigration, admission, scholarship, banking, medical, government, employment...). Look at the attached image(s) of a form. Multiple images are consecutive pages of the same form.

Rules:
- List every field the applicant fills in, in reading order. Skip sections marked for office, official or staff use, and signatures or approvals by officials.
- A field is one place the applicant writes or ticks something: each blank line, box or checkbox group is exactly one field. Text printed after a blank (units, currency or periods such as "Per Month", "Rs.", "%", "kg", "years") belongs to the field before it: mention it in that field's explanation, never list it as its own field.
- Headings, instructions, notes and lists of documents to attach are not fields; put attachments in "checklist" or "warnings" instead.
- "label": the field's printed label copied character-for-character, without its item number (e.g. "Date of Birth", not "2. Date of Birth"). "evidence": quote the printed text that proves the field exists.
- NEVER suggest, guess or invent a value for any field. In "you_need", describe what information or document the user must provide, not the answer itself.
- "status": "filled" if something is already written in the field, "empty" if blank, "unclear" if unreadable. Never guess unreadable text. If "filled", "you_need" is an empty list and "explanation" says what to double-check.
- "required": true or false only if the form marks it (e.g. *, "required", "optional"); otherwise null.
- "warnings": short plain-language alerts about deadlines, penalties, fees, signatures or attachments printed on the form, e.g. "Deadline: 15 Dec, 5 PM. Late forms are rejected." Do not just repeat field labels. Empty list if none.
- "checklist": ordered steps to complete and submit the form.
- Write "purpose", "explanation", "you_need", "checklist" and "warnings" in LANGUAGE. Keep "form_title", "label" and "evidence" exactly as printed.
- Plain words: write for someone who has never filled a form like this and may not read well. Use everyday words a 12-year-old knows, talk to the reader as "you", and keep each sentence under 15 words. "explanation" is 1 or 2 short sentences: what the field asks for, then why or how if it helps.
- Never use jargon or legal words. If the form uses a term, abbreviation or acronym (e.g. "CNIC", "PTCL", "dependent", "attested", "guardian", "domicile"), say what it means in simple words.
  Bad: "Provide the residential locality pertaining to your domicile."
  Good: "Write the name of the area where you live."
- "you_need" items are short, concrete things, e.g. "Your national ID card" or "Your father's monthly salary".
- Never use emojis or em dashes. Use commas or full stops instead.

Output JSON Lines: one JSON object per line, no markdown, no blank lines, in this order:
{"type": "form", "form_title": "...", "purpose": "..."}
{"type": "field", "label": "...", "evidence": "...", "explanation": "...", "required": true, "status": "empty", "you_need": ["..."]}
(one "field" line per field)
{"type": "summary", "checklist": ["..."], "warnings": ["..."]}
"""

LOCATE_PROMPT = """You are given the page image(s) of a form (the first image is page 0) and a list of its fields.
For each field, find the blank space where the applicant writes the answer: the empty line, box or area next to or below its label. Do not include the label text itself.
Reply with ONLY one JSON object: {"boxes": [{"id": <field id>, "page": <page index>, "box_2d": [ymin, xmin, ymax, xmax]}]} with coordinates normalized to 0-1000. Leave out fields you cannot find.
Fields:
"""

EVENT_MODELS = {"form": FormHeader, "field": Field, "summary": Summary}
# the portal shows no emojis or em dashes; the model sometimes writes them anyway
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿️‍]")


def _plain(text: str) -> str:
    text = text.replace(" — ", ", ").replace("—", ", ").replace("–", "-")
    return re.sub(" {2,}", " ", _EMOJI.sub("", text))


@cache
def _gemini():
    from google import genai

    return genai.Client()  # reads GEMINI_API_KEY


def _gemini_args(prompt: str, images: list[tuple[bytes, str]]) -> dict:
    from google.genai import types

    # "minimal" cuts ~70% of latency on gemma-4-26b-a4b-it; some models reject the setting, so it's opt-in
    level = os.getenv("GEMMA_THINKING")
    return {
        "model": os.environ["GEMMA_MODEL"],
        "contents": [*(types.Part.from_bytes(data=b, mime_type=m) for b, m in images), prompt],
        "config": types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level=level)) if level else None,
    }


def _ollama(prompt: str, images: list[tuple[bytes, str]], **options) -> urllib.request.Request:
    body = json.dumps({
        "model": os.environ["GEMMA_MODEL"],
        "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(b).decode() for b, _ in images]}],
        **options,
    }).encode()
    url = os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/chat"
    return urllib.request.Request(url, body, {"Content-Type": "application/json"})


def _use_ollama() -> bool:
    return os.getenv("PROVIDER", "gemini") == "ollama"


def _stream(prompt: str, images: list[tuple[bytes, str]]) -> Iterator[str]:
    if _use_ollama():
        with urllib.request.urlopen(_ollama(prompt, images, stream=True), timeout=300) as r:
            for line in r:
                yield json.loads(line)["message"]["content"]
        return
    for chunk in _gemini().models.generate_content_stream(**_gemini_args(prompt, images)):
        yield chunk.text or ""


def _call(prompt: str, images: list[tuple[bytes, str]]) -> str:
    if _use_ollama():
        with urllib.request.urlopen(_ollama(prompt, images, stream=False, format="json"), timeout=300) as r:
            return json.load(r)["message"]["content"]
    return _gemini().models.generate_content(**_gemini_args(prompt, images)).text or ""


def parse_lines(chunks: Iterable[str]) -> Iterator[dict]:
    """Turn streamed model text into validated events; field events get sequential ids."""
    buf, next_id = "", 0
    for chunk in chain(chunks, ["\n"]):
        buf += chunk
        *lines, buf = buf.split("\n")
        for line in lines:
            try:
                raw = json.loads(_plain(line))
                kind = raw.pop("type")
                event = {"type": kind, **EVENT_MODELS[kind].model_validate(raw).model_dump()}
            except (ValueError, KeyError, TypeError, AttributeError):
                # shortcut: malformed lines (fences, chatter, bad fields) are dropped, not retried; add a repair pass if fields go missing
                continue
            if kind == "field":
                event["id"], next_id = next_id, next_id + 1
            yield event


def parse_boxes(text: str, n_pages: int, ids: set[int]) -> dict[int, Location]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in model reply")
    out = {}
    for b in json.loads(m.group(0)).get("boxes", []):
        try:
            y0, x0, y1, x1 = (min(max(float(v) / 1000, 0.0), 1.0) for v in b["box_2d"])
            loc = Location(id=b["id"], page=b.get("page", 0), box=[min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)])
        except (ValueError, KeyError, TypeError):
            continue
        if loc.id in ids and loc.page < n_pages and loc.box[2] > loc.box[0] and loc.box[3] > loc.box[1]:
            out[loc.id] = loc
    return out


def explain(images: list[tuple[bytes, str]], language: str) -> Iterator[dict]:
    """Agent 1: streams form, field and summary events as Gemma writes them."""
    yield from parse_lines(_stream(EXPLAIN_PROMPT.replace("LANGUAGE", language), images))


def locate(images: list[tuple[bytes, str]], fields: list[FieldRef]) -> dict[int, Location]:
    """Agent 2: Gemma vision coordinates for each field's answer area."""
    refs = json.dumps([{"id": f.id, "label": f.label} for f in fields], ensure_ascii=False)
    return parse_boxes(_call(LOCATE_PROMPT + refs, images), len(images), {f.id for f in fields})

import json
import os
import threading
import time
from collections import defaultdict, deque
from typing import Annotated

from dotenv import load_dotenv

load_dotenv()

import pydantic  # noqa: E402
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from google.genai import errors  # noqa: E402

from .gemma import explain, locate  # noqa: E402
from .pdf import as_pdf, data_urls, fill_pdf, overlapping, text_locations, to_images  # noqa: E402
from .schemas import FieldRef, FillValue, LocateResult  # noqa: E402

ALLOWED = {"image/png", "image/jpeg", "image/webp", "application/pdf"}
MAX_BYTES = 8 * 1024 * 1024
FIELD_REFS = pydantic.TypeAdapter(Annotated[list[FieldRef], pydantic.Field(max_length=300)])
FILL_VALUES = pydantic.TypeAdapter(Annotated[list[FillValue], pydantic.Field(max_length=300)])
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "5"))  # Gemma calls per visitor per window, for each endpoint
RATE_WINDOW = 300  # seconds

app = FastAPI(title="DocPal")
app.add_middleware(
    CORSMiddleware,
    # browsers send the origin without a trailing slash, so strip any that were pasted in
    allow_origins=[o.strip().rstrip("/") for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


def read_upload(file: UploadFile) -> bytes:
    if file.content_type not in ALLOWED:
        raise HTTPException(400, "Upload a PDF, PNG, JPEG or WebP file.")
    data = file.file.read(MAX_BYTES + 1)  # kept in memory only, never written to disk
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File must be 8 MB or smaller.")
    return data


# shortcut: counts live in this process's memory, so they reset on restart and aren't shared between
# serverless instances; move them to a shared store (e.g. Upstash Redis) if abuse gets past this
_calls: dict[str, deque[float]] = defaultdict(deque)
_calls_lock = threading.Lock()  # endpoints run in a threadpool


def rate_limit(request: Request, bucket: str) -> None:
    """Allow RATE_LIMIT Gemma calls per visitor per RATE_WINDOW, so nobody can burn the model quota."""
    # X-Real-IP is set by nginx (Docker) and by Vercel's edge; without a proxy, use the socket address
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    now = time.monotonic()
    with _calls_lock:
        calls = _calls[f"{bucket}:{ip}"]
        while calls and now - calls[0] > RATE_WINDOW:
            calls.popleft()
        if len(calls) >= RATE_LIMIT:
            wait = int(RATE_WINDOW - (now - calls[0])) + 1
            raise HTTPException(429, f"You've reached the limit of {RATE_LIMIT} forms every 5 minutes. "
                                     f"Please try again in {max(1, round(wait / 60))} min.", headers={"Retry-After": str(wait)})
        calls.append(now)
        if len(_calls) > 10_000:  # forget visitors whose window has passed, so memory stays small
            for key in [k for k, v in _calls.items() if not v or now - v[-1] > RATE_WINDOW]:
                del _calls[key]


def parse_json(adapter: pydantic.TypeAdapter, raw: str, what: str):
    try:
        return adapter.validate_json(raw)
    except pydantic.ValidationError:
        raise HTTPException(400, f"Invalid {what}.")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/analyze")
def analyze_form(request: Request, file: UploadFile = File(...), language: str = Form("English")):
    """Agent 1. NDJSON stream: pages first (instant preview), then form, field... and summary events."""
    rate_limit(request, "analyze")
    data = read_upload(file)
    images = to_images(data, file.content_type)
    language = language if language in {"English", "Urdu"} else "English"  # goes into the prompt, so only known values

    # sync generator: Starlette runs it in a threadpool, so the blocking model stream doesn't stall the server
    def events():
        yield {"type": "pages", "pages": data_urls(images)}
        try:
            yield from explain(images, language)
        except errors.APIError:
            yield {"type": "error", "message": "Gemma is busy right now. Please try again in a moment."}

    return StreamingResponse((json.dumps(e, ensure_ascii=False) + "\n" for e in events()), media_type="application/x-ndjson")


@app.post("/api/locate", response_model=LocateResult)
def locate_form(request: Request, file: UploadFile = File(...), fields: str = Form(...)):
    """Agent 2. Exact text-layer positions for digital PDFs; Gemma vision boxes for everything else."""
    data = read_upload(file)
    refs = parse_json(FIELD_REFS, fields, "fields")
    found, not_fields = text_locations(data, refs) if file.content_type == "application/pdf" else ({}, set())
    missing = [r for r in refs if r.id not in found and r.id not in not_fields]
    if missing:
        rate_limit(request, "locate")  # only counts when Gemma is actually called
        try:
            found |= locate(to_images(data, file.content_type), missing)
        except (ValueError, errors.APIError):
            if not found:
                raise HTTPException(503, "Couldn't place the fields on the form. Please try again.")
            # keep the exact text matches; the rest just get no highlight
    ordered = [found[r.id] for r in refs if r.id in found]  # reading order: the first field keeps a shared blank
    not_fields |= overlapping(ordered)
    return LocateResult(locations=[loc for loc in ordered if loc.id not in not_fields], not_fields=sorted(not_fields))


@app.post("/api/fill")
def fill_form(file: UploadFile = File(...), values: str = Form(...)):
    data = read_upload(file)
    pdf = fill_pdf(as_pdf(data, file.content_type), parse_json(FILL_VALUES, values, "field values"))
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="docpal-filled.pdf"'})

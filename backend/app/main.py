import json
import os
from typing import Annotated

from dotenv import load_dotenv

load_dotenv()

import pydantic  # noqa: E402
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile  # noqa: E402
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

app = FastAPI(title="DocPal")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
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


def parse_json(adapter: pydantic.TypeAdapter, raw: str, what: str):
    try:
        return adapter.validate_json(raw)
    except pydantic.ValidationError:
        raise HTTPException(400, f"Invalid {what}.")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/analyze")
def analyze_form(file: UploadFile = File(...), language: str = Form("English")):
    """Agent 1. NDJSON stream: pages first (instant preview), then form, field... and summary events."""
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
def locate_form(file: UploadFile = File(...), fields: str = Form(...)):
    """Agent 2. Exact text-layer positions for digital PDFs; Gemma vision boxes for everything else."""
    data = read_upload(file)
    refs = parse_json(FIELD_REFS, fields, "fields")
    found, not_fields = text_locations(data, refs) if file.content_type == "application/pdf" else ({}, set())
    missing = [r for r in refs if r.id not in found and r.id not in not_fields]
    if missing:
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

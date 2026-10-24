# DocPal backend

FastAPI service that runs DocPal's two Gemma 4 agents and handles all PDF work. See the root README for the product overview and API table.

## Commands

Run from `backend/`, with the virtualenv in `.venv`:

```bash
.venv\Scripts\python -m pytest -q             # all tests, no network or API key needed
.venv\Scripts\uvicorn app.main:app --reload   # dev server on :8000
docker compose up -d --build backend          # from repo root, rebuild the container
```

Config is read from `.env` by `load_dotenv()` at the top of `main.py`. Never commit `.env` and never print `GEMINI_API_KEY`.

## Layout

- `app/main.py`: routes and request checks only. `read_upload` enforces allowed types and the 8 MB limit; `parse_json` validates JSON form fields with Pydantic TypeAdapters and returns a 400 on bad input. The language is limited to `English` and `Urdu` because it goes into the prompt.
- `app/gemma.py`: both agents.
  - `explain()` streams Agent 1's output.
  - `locate()` is Agent 2's vision fallback.
  - `_stream` and `_call` switch between the Gemini API and Ollama based on `PROVIDER`.
  - `parse_lines` turns streamed text into validated events and gives each field its `id`.
  - `parse_boxes` reads Gemma boxes.
- `app/pdf.py`: everything PyMuPDF.
  - `open_pdf` (checks), `to_images` (2x render), `as_pdf` (image to PDF).
  - `text_locations`: exact positions from the text layer, plus ids that are unit suffixes.
  - `overlapping`: duplicate guardrail.
  - `fill_pdf`: sets existing text widgets, or adds new `docpal_*` widgets.
- `app/schemas.py`: Pydantic models. `Field`, `FormHeader` and `Summary` are Agent 1's output; `FieldRef` and `Location` are Agent 2's input and output; `FillValue` is the fill input.

## Request flow

1. `POST /api/analyze` returns NDJSON: a `pages` event first, then `form`, `field` and `summary` events as Gemma writes them. Gemma API errors become an `error` event, because the HTTP status is already sent.
2. `POST /api/locate` runs `text_locations` for PDFs first, sends only the fields still missing to Gemma's `locate`, then applies `overlapping`. Rejected ids go in `not_fields`, and the frontend removes those cards.
3. `POST /api/fill` converts images to PDFs with `as_pdf`, then calls `fill_pdf`.

## Rules

- **Stateless and no disk writes.** Uploads stay in memory; the client re-sends the file on every call. Don't add storage.
- **Endpoints are sync `def`.** FastAPI runs them, and the streaming generator, in a threadpool, so the blocking Gemma calls don't stall the server. Don't make them `async` without making the model calls async.
- **Coordinates** are page-relative fractions `[x0, y0, x1, y1]` in the range 0 to 1. Gemma returns `box_2d` as `[ymin, xmin, ymax, xmax]` on a 0 to 1000 scale; `parse_boxes` converts it.
- **The AI never fills in answers.** Prompts must keep forbidding invented values; `you_need` describes what's needed, never the answer itself.
- **Plain language.** Explanations stay short and jargon-free (see the rules in `EXPLAIN_PROMPT`). No emojis or em dashes anywhere: `_plain()` strips them from model output before parsing, and new UI text or prompts must not add them.
- **Nothing form-specific.** Prompts and heuristics must work for any form. Don't hard-code labels from the sample forms.
- **Add a test** in `tests/` for any change to parsing, location or guardrails.

## Known quirks

- `GEMMA_THINKING=minimal` makes `gemma-4-26b-a4b-it` about 4x faster. `gemma-4-31b-it` rejects both `thinking_level` and `thinking_budget`, so leave the variable empty for that model.
- Gemma writes straight quotes but PDFs often contain curly ones (Guardian’s). `text_locations` tries both spellings.
- PyMuPDF's built-in fonts can't encode characters like `’`. Tests that need them use `insert_htmlbox`.
- Rotated pages aren't supported (there's a `shortcut:` comment in `pdf.py`).
- Malformed model lines are dropped, not retried (there's a `shortcut:` comment in `parse_lines`).

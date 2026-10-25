# DocPal frontend

React 18 + Vite single page app, styled with Radix Themes. See the root README for the product overview and API table.

## Commands

Run from `frontend/`:

```bash
npm run dev          # http://localhost:5173, Vite forwards /api to localhost:8000
npm run build        # production build into dist/, run it to check for errors
docker compose up -d --build frontend   # from repo root, rebuild the nginx container (port 3000)
```

There are no frontend tests. Check changes with `npm run build` and in the browser.

## Layout

- `src/main.jsx`: wraps the app in `<Theme accentColor="indigo" grayColor="slate" radius="medium" scaling="110%">`. The theme follows the system's light or dark setting.
- `src/App.jsx`: owns all state and the flow:
  - `handleAnalyze` streams Agent 1 (`analyzeForm`), then calls Agent 2 (`locateFields`) and drops the ids returned in `not_fields`.
  - `download` posts the typed values to `/api/fill`.
- `src/api.js`: `post` helper, the NDJSON stream reader, `locateFields` and `fillForm`. Error messages come from the API's `detail` field.
- `src/components/`:
  - `Upload`: drop zone and the English/Urdu dropdown.
  - `FormPreview`: page images with absolutely positioned highlight buttons and a live preview of each answer: text on the line, one letter per box, or a tick in each picked option's box.
  - `FieldList`: one card per field. Its `Answer` is a text input (capped to the number of letter boxes when the form has them), or radio buttons / checkboxes for choice fields.
  - `Checklist`: tick-off list with a copy button.
- `src/index.css`: only layout, the drop zone and the preview overlay. Everything else comes from Radix components and tokens.
- `nginx.conf`: serves the build and forwards `/api/` to the backend. It needs `proxy_buffering off` for the stream, a 300 s read timeout and a 10 MB body limit. Keep those if you edit it.

## State model

- Fields are keyed by the `id` the backend assigns while streaming. `values`, `locations` and `active` all use that id, never an array index.
- A text field's value is a string; a choice field's value is an array of the picked option texts (one item for single choice). `download` turns picked options into `{page, box, tick: true}` entries.
- `phase` moves through `idle`, `reading`, `locating` and `done`. The download button appears only at `done`, and only when at least one field has a location.
- Boxes are page-relative fractions `[x0, y0, x1, y1]`, so `FormPreview` positions overlays with percentages. `label_box` exists only for text-layer PDFs.
- Clicking a highlight focuses its card's input; focusing a card scrolls its highlight into view.

## Rules

- **Use Radix Themes components** (`Card`, `Button`, `TextField`, `Badge`, `Callout`, `Select`, `Checkbox`, `Heading`, `Text`) and their size, color and spacing props before writing CSS. Custom CSS uses Radix tokens (`var(--accent-9)`, `var(--space-3)`, `var(--radius-3)`).
- **Overlay colors are fixed light-theme indigo** (`#3e63dd`). The form image stays white in dark mode, so theme-dependent colors would be unreadable.
- **Fonts:** Geist (body) and Caveat (handwritten `.brand` title and `.hand` punchline only) are loaded in `index.html`. Geist is set through `--default-font-family` in `index.css`. Keep Caveat for decoration; explanations and inputs stay in Geist so they're easy to read.
- **Notebook look:** the graph-paper grid is the `body` background in `index.css`, with its own dark-mode colors. The Theme uses `hasBackground={false}` so the grid shows, and `panelBackground="solid"` so cards stay readable. Anything translucent placed on the grid (like callouts) needs a solid layer under it.
- **Right-to-left text:** any element showing AI-written text gets `dir="auto"`, so Urdu displays right to left.
- **Copy:** keep it simple and clear. No emojis or em dashes in UI text.
- **Accessibility:** keep `aria-live` on the results column, `role="alert"` on errors and `aria-label` on highlight buttons.
- **Privacy:** never store the uploaded file or answers (no localStorage). The file lives only in React state.

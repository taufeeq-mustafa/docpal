// a trailing slash would make "//api/..." URLs, which Vercel redirects and browsers then block
const BASE = (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '')

async function post(path, fields) {
  const body = new FormData()
  for (const [k, v] of Object.entries(fields)) body.append(k, v)
  const res = await fetch(`${BASE}${path}`, { method: 'POST', body })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `Server error ${res.status}`)
  }
  return res
}

// Agent 1: calls onEvent for each streamed line: pages, form, field, summary, error
export async function analyzeForm(file, language, onEvent) {
  const res = await post('/api/analyze', { file, language })
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += value
    const lines = buf.split('\n')
    buf = lines.pop()
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line))
  }
  if (buf.trim()) onEvent(JSON.parse(buf))
}

// Agent 2: returns { locations: [{ id, page, box, label_box, cells, choices }], not_fields: [id] }
export async function locateFields(file, fields) {
  const refs = fields.map(({ id, label, evidence, kind, options }) => ({ id, label, evidence, kind, options }))
  return (await post('/api/locate', { file, fields: JSON.stringify(refs) })).json()
}

// values: [{ page, box, value, cells }] for text, [{ page, box, tick: true }] per picked option; returns the filled PDF as a Blob
export async function fillForm(file, values) {
  return (await post('/api/fill', { file, values: JSON.stringify(values) })).blob()
}

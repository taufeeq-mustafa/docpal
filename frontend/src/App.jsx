import { useState } from 'react'
import { Box, Button, Callout, Container, Flex, Heading, Spinner, Text } from '@radix-ui/themes'
import { DownloadIcon, ExclamationTriangleIcon, InfoCircledIcon } from '@radix-ui/react-icons'
import { analyzeForm, fillForm, locateFields } from './api.js'
import Upload from './components/Upload.jsx'
import FormPreview from './components/FormPreview.jsx'
import FieldList from './components/FieldList.jsx'
import Checklist from './components/Checklist.jsx'

export default function App() {
  const [file, setFile] = useState(null)
  const [pages, setPages] = useState(null)
  const [form, setForm] = useState(null)
  const [fields, setFields] = useState([])
  const [summary, setSummary] = useState(null)
  const [locations, setLocations] = useState({})
  const [values, setValues] = useState({})
  const [active, setActive] = useState(null)
  const [phase, setPhase] = useState('idle') // idle | reading | locating | done
  const [error, setError] = useState('')

  async function handleAnalyze(f, language) {
    setFile(f)
    setPages(null)
    setForm(null)
    setFields([])
    setSummary(null)
    setLocations({})
    setValues({})
    setActive(null)
    setError('')
    setPhase('reading')
    const found = []
    try {
      await analyzeForm(f, language, (e) => {
        if (e.type === 'pages') setPages(e.pages)
        else if (e.type === 'form') setForm(e)
        else if (e.type === 'summary') setSummary(e)
        else if (e.type === 'field') {
          found.push(e)
          setFields([...found])
        } else if (e.type === 'error') throw new Error(e.message)
      })
      if (!found.length) throw new Error("Couldn't find any fields on this form. Try a clearer file.")
      setPhase('locating')
      const { locations: locs, not_fields: rejected } = await locateFields(f, found)
      setLocations(Object.fromEntries(locs.map((l) => [l.id, l])))
      // guardrails found units ("Per Month") or duplicates that aren't real fields
      setFields(found.filter((x) => !rejected.includes(x.id)))
    } catch (e) {
      setError(e.message)
    } finally {
      setPhase('done')
    }
  }

  function pickFromForm(id) {
    setActive(id)
    const card = document.getElementById(`field-${id}`)
    card?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    card?.querySelector('input')?.focus({ preventScroll: true })
  }

  function pickFromCard(id) {
    setActive(id)
    document.getElementById(`hl-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }

  async function download() {
    const payload = fields.flatMap((f) => {
      const loc = locations[f.id]
      const value = values[f.id]
      if (!loc || !value?.length) return []
      // choices: one tick per picked option; text: the answer, split into letter boxes when the form has them
      if (f.kind === 'choice') return value.filter((o) => loc.choices[o]).map((o) => ({ page: loc.page, box: loc.choices[o], tick: true }))
      return value.trim() ? [{ page: loc.page, box: loc.box, value, cells: loc.cells }] : []
    })
    try {
      const url = URL.createObjectURL(await fillForm(file, payload))
      const a = document.createElement('a')
      a.href = url
      a.download = `${file.name.replace(/\.[^.]+$/, '')}-filled.pdf`
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) {
      setError(e.message)
    }
  }

  const busy = phase === 'reading' || phase === 'locating'
  const status = phase === 'reading'
    ? (fields.length ? `Explaining fields… ${fields.length} so far` : 'Reading your form…')
    : phase === 'locating' ? 'Finding where each answer goes on the form…' : ''

  return (
    <Container size="4" px="4" py="5">
      <Flex direction="column" gap="2" mb="5">
        <Heading size="9" weight="bold" className="brand">DocPal</Heading>
        <Heading as="p" size="8" weight="medium" color="indigo" className="hand">Any form. Plain words. Done right.</Heading>
        <Text size="4" color="gray">Understand every field in seconds, fill it in on the spot, and download it ready to submit.</Text>
      </Flex>
      <div className="layout">
        <Flex direction="column" gap="3" className="left">
          <Upload onAnalyze={handleAnalyze} loading={busy} showPreview={!pages} />
          {pages && (
            <FormPreview pages={pages} fields={fields} locations={locations} values={values} active={active} onPick={pickFromForm} />
          )}
        </Flex>
        <Flex direction="column" gap="3" aria-live="polite">
          {status && (
            <Callout.Root size="2">
              <Callout.Icon><Spinner /></Callout.Icon>
              <Callout.Text>{status}</Callout.Text>
            </Callout.Root>
          )}
          {error && (
            <Callout.Root size="2" color="red" role="alert">
              <Callout.Icon><ExclamationTriangleIcon /></Callout.Icon>
              <Callout.Text>{error}</Callout.Text>
            </Callout.Root>
          )}
          {phase === 'idle' && (
            <Callout.Root size="2">
              <Callout.Icon><InfoCircledIcon /></Callout.Icon>
              <Callout.Text>Upload any form (immigration, admission, scholarship, banking…) as a PDF, photo or screenshot.</Callout.Text>
            </Callout.Root>
          )}
          {form && (
            <Flex justify="between" align="start" gap="3" wrap="wrap">
              <Box style={{ flex: 1, minWidth: 0 }}>
                <Heading size="6" mb="1">{form.form_title}</Heading>
                <Text as="p" size="3" color="gray" dir="auto">{form.purpose}</Text>
              </Box>
              {phase === 'done' && Object.keys(locations).length > 0 && (
                <Button size="3" onClick={download}><DownloadIcon /> Download filled PDF</Button>
              )}
            </Flex>
          )}
          {summary?.warnings.map((w, i) => (
            <Callout.Root key={i} color="amber" size="2">
              <Callout.Icon><ExclamationTriangleIcon /></Callout.Icon>
              <Callout.Text dir="auto">{w}</Callout.Text>
            </Callout.Root>
          ))}
          <FieldList
            fields={fields}
            locations={locations}
            locating={phase !== 'done'}
            values={values}
            active={active}
            onPick={pickFromCard}
            onChange={(id, v) => setValues((prev) => ({ ...prev, [id]: v }))}
          />
          {summary && <Checklist items={summary.checklist} />}
        </Flex>
      </div>
      <Text as="p" size="2" color="gray" mt="6">Your file is processed in memory and never stored. Powered by Gemma 4 (open weights).</Text>
    </Container>
  )
}

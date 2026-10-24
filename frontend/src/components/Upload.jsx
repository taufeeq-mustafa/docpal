import { useEffect, useState } from 'react'
import { Button, Card, Flex, Select, Text } from '@radix-ui/themes'
import { UploadIcon } from '@radix-ui/react-icons'

const LANGUAGES = ['English', 'Urdu']

export default function Upload({ onAnalyze, loading, showPreview }) {
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState('')
  const [language, setLanguage] = useState('English')

  useEffect(() => {
    if (!file) return
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  function pick(f) {
    if (f && (f.type.startsWith('image/') || f.type === 'application/pdf')) setFile(f)
  }

  return (
    <Card size="2">
      <Flex direction="column" gap="3">
        <label
          className={`drop${showPreview ? '' : ' compact'}`}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); pick(e.dataTransfer.files[0]) }}
        >
          {!(preview && showPreview) && (
            <Flex direction="column" align="center" gap="2">
              <UploadIcon width="24" height="24" />
              <Text size="3" color="gray">Drop a PDF, photo or screenshot of any form, or click to choose</Text>
            </Flex>
          )}
          {preview && showPreview && file.type === 'application/pdf' && <embed src={preview} type="application/pdf" className="pdf" title="Your uploaded form" />}
          {preview && showPreview && file.type !== 'application/pdf' && <img src={preview} alt="Your uploaded form" />}
          <input type="file" accept="application/pdf,image/png,image/jpeg,image/webp" hidden onChange={(e) => pick(e.target.files[0])} />
        </label>
        {file && <Text size="2" color="gray">{file.name} · click or drop to replace</Text>}
        <Flex gap="3" align="center" wrap="wrap">
          <Text size="3">Explain in</Text>
          <Select.Root size="3" value={language} onValueChange={setLanguage}>
            <Select.Trigger aria-label="Explanation language" />
            <Select.Content>
              {LANGUAGES.map((l) => <Select.Item key={l} value={l}>{l}</Select.Item>)}
            </Select.Content>
          </Select.Root>
          <Button size="3" disabled={!file} loading={loading} onClick={() => onAnalyze(file, language)}>
            Explain this form
          </Button>
        </Flex>
      </Flex>
    </Card>
  )
}

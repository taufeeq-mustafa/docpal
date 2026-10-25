import { Badge, Card, CheckboxGroup, Flex, Heading, RadioGroup, Text, TextField } from '@radix-ui/themes'

const REQUIRED = { true: ['Required', 'indigo'], false: ['Optional', 'gray'], null: ['Not stated', 'gray'] }
const STATUS_COLOR = { empty: 'gray', filled: 'green', unclear: 'amber' }

// text fields take a string; choice fields take an array of the picked option texts
function Answer({ field: f, cells, value, onPick, onChange }) {
  if (f.kind === 'choice') {
    const Group = f.multiple ? CheckboxGroup : RadioGroup
    return (
      <div onFocus={() => onPick(f.id)}>
        <Text as="div" size="2" weight="medium" mb="2">{f.multiple ? 'Tick all that apply' : 'Choose one'}</Text>
        <Group.Root
          size="3"
          value={f.multiple ? (value ?? []) : (value?.[0] ?? '')}
          onValueChange={(v) => onChange(f.id, f.multiple ? v : [v])}
        >
          {f.options.map((o) => <Group.Item key={o} value={o}><span dir="auto">{o}</span></Group.Item>)}
        </Group.Root>
      </div>
    )
  }
  return (
    <label>
      <Text as="div" size="2" weight="medium" mb="1">Your answer</Text>
      <TextField.Root
        size="3"
        value={value ?? ''}
        maxLength={cells || 500}
        placeholder={cells ? `One letter per box, up to ${cells}` : 'Type your answer'}
        onFocus={() => onPick(f.id)}
        onChange={(e) => onChange(f.id, e.target.value)}
      />
    </label>
  )
}

export default function FieldList({ fields, locations, locating, values, active, onPick, onChange }) {
  return (
    <Flex direction="column" gap="3">
      {fields.map((f) => {
        const [required, requiredColor] = REQUIRED[f.required]
        return (
          <Card key={f.id} id={`field-${f.id}`} className={`field${f.id === active ? ' active' : ''}`} onClick={() => onPick(f.id)}>
            <Flex direction="column" gap="2">
              <Flex align="center" gap="2" wrap="wrap">
                <Heading as="h3" size="4" style={{ flex: 1 }}>{f.label}</Heading>
                <Badge size="2" color={STATUS_COLOR[f.status]} variant="soft">{f.status}</Badge>
                <Badge size="2" color={requiredColor} variant="outline">{required}</Badge>
              </Flex>
              <Text size="3" dir="auto">{f.explanation}</Text>
              {f.you_need.length > 0 && (
                <Flex gap="2" wrap="wrap" align="center">
                  <Text size="2" weight="bold">You need:</Text>
                  {f.you_need.map((n, j) => <Badge key={j} size="2" variant="surface" radius="full" className="chip" dir="auto">{n}</Badge>)}
                </Flex>
              )}
              <Answer field={f} cells={locations[f.id]?.cells?.length ?? 0} value={values[f.id]} onPick={onPick} onChange={onChange} />
              {!locations[f.id] && (
                <Text size="2" color={locating ? 'gray' : 'amber'}>
                  {locating ? 'Finding this field on the form…' : "Couldn't place this on the form, so it won't be in the download."}
                </Text>
              )}
              <Text size="2" color="gray">Seen on form: “{f.evidence}”</Text>
            </Flex>
          </Card>
        )
      })}
    </Flex>
  )
}

import { useState } from 'react'
import { Button, Card, Checkbox, Flex, Heading, Text } from '@radix-ui/themes'
import { CheckIcon, CopyIcon } from '@radix-ui/react-icons'

export default function Checklist({ items }) {
  const [done, setDone] = useState({})
  const [copied, setCopied] = useState(false)

  async function copy() {
    await navigator.clipboard.writeText(items.map((t, i) => `${done[i] ? '[x]' : '[ ]'} ${t}`).join('\n'))
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <Card size="2">
      <Flex justify="between" align="center" mb="3">
        <Heading as="h3" size="4">Your checklist</Heading>
        <Button variant="soft" size="2" onClick={copy}>
          {copied ? <CheckIcon /> : <CopyIcon />} {copied ? 'Copied' : 'Copy'}
        </Button>
      </Flex>
      <Flex direction="column" gap="2">
        {items.map((t, i) => (
          <Text as="label" size="3" key={i}>
            <Flex gap="2" align="start">
              <Checkbox size="2" mt="1" checked={!!done[i]} onCheckedChange={() => setDone({ ...done, [i]: !done[i] })} />
              <span className={done[i] ? 'done' : ''} dir="auto">{t}</span>
            </Flex>
          </Text>
        ))}
      </Flex>
    </Card>
  )
}

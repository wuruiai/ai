export interface SSEEvent {
  event: string
  data: any
  id?: string
}

export function parseSSEStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  onEvent: (event: SSEEvent) => void
): Promise<void> {
  const decoder = new TextDecoder()
  let buffer = ''

  return new Promise(async (resolve, reject) => {
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        let currentEvent = ''
        let currentData = ''
        let currentId = ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7)
          } else if (line.startsWith('data: ')) {
            currentData = line.slice(6)
          } else if (line.startsWith('id: ')) {
            currentId = line.slice(4)
          } else if (line === '') {
            if (currentEvent || currentData) {
              onEvent({
                event: currentEvent,
                data: JSON.parse(currentData),
                id: currentId || undefined,
              })
              currentEvent = ''
              currentData = ''
              currentId = ''
            }
          }
        }
      }
      resolve()
    } catch (error) {
      reject(error)
    }
  })
}

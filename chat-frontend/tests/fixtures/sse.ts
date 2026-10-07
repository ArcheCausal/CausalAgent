export function responseFromChunks(chunks: string[], contentType = 'text/event-stream'): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return new Response(stream, { headers: { 'content-type': contentType } })
}

export function responseFromBytes(chunks: Uint8Array[], contentType = 'text/event-stream'): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk)
      controller.close()
    },
  })
  return new Response(stream, { headers: { 'content-type': contentType } })
}

export function sseBlock(
  id: number | null,
  type: string,
  data: Record<string, unknown>,
  lineEnding = '\n',
): string {
  const lines = [
    ...(id === null ? [] : [`id: ${id}`]),
    `event: ${type}`,
    `data: ${JSON.stringify(data)}`,
    '',
  ]
  return `${lines.join(lineEnding)}${lineEnding}`
}

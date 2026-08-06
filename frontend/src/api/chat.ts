/**
 * SSE 客户端：消费后端 /api/v1/chat/stream 流式接口
 *
 * 事件契约（与后端 backend/api/v1/chat.py + core/sse.py 对齐）：
 *   event: start      data: { thread_id }
 *   event: status     data: { status }
 *   event: token      data: { delta }
 *   event: citation   data: { index, source_id, source_name, page, content }
 *   event: done       data: { message_id }
 *   event: error      data: { code, message }
 *   event: warning    data: { message }
 *
 * 设计原则：
 *   - 只做"行级解析 + 事件分发"，不做任何业务判断（重试/超时由调用方包）
 *   - 每个回调都允许抛出；catch 后会调用 onError 并向上抛，让 ChatView 决定是否终止
 *   - 缓冲处理 SSE 协议要求的 "\n\n" 事件分隔，未到事件边界的数据留在 buffer
 *   - 换行已归一化（兼容 CRLF / LF），详见 streamChat 内
 */

export interface StartEvent {
  thread_id: string
}

export interface StatusEvent {
  status: string
}

export interface TokenEvent {
  delta: string
}

export interface CitationEvent {
  index: number
  source_id: string
  source_name?: string
  page?: number | null
  content?: string
  snippet?: string
  score?: number
  [k: string]: unknown
}

export interface CitationVerdictEvent {
  items: Array<{ index: number; verified: boolean }>
}

export interface DoneEvent {
  message_id: string
}

export interface ErrorEvent {
  code: string
  message: string
}

export interface WarningEvent {
  message: string
}

export interface StreamChatOptions {
  query: string
  threadId: string
  onStart?: (e: StartEvent) => void
  onStatus?: (e: StatusEvent) => void
  onToken: (e: TokenEvent) => void
  onCitation: (e: CitationEvent) => void
  onCitationVerdict?: (e: CitationVerdictEvent) => void
  onDone: (e: DoneEvent) => void
  onError: (e: ErrorEvent) => void
  onWarning?: (e: WarningEvent) => void
  signal?: AbortSignal
}

const EVENT_SEP = '\n\n'

/** 单行解析：识别 "event: xxx" / "data: {...}" 行，返回结构化字段或 null。 */
export function parseSseLine(line: string): { event?: string; data?: string } | null {
  const trimmed = line.trim()
  if (!trimmed) return null
  if (trimmed.startsWith(':')) return null // 注释行
  const colon = trimmed.indexOf(':')
  if (colon === -1) return null
  const field = trimmed.slice(0, colon)
  let value = trimmed.slice(colon + 1)
  if (value.startsWith(' ')) value = value.slice(1)
  if (field === 'event') return { event: value }
  if (field === 'data') return { data: value }
  return null
}

/** 把一个完整事件块（多行以 \n 分隔）聚合为 { event, data }，data 自动 JSON.parse。 */
export function parseSseBlock(block: string): { event: string; data: unknown } | null {
  const lines = block.split('\n')
  let event = 'message'
  const dataLines: string[] = []
  for (const line of lines) {
    const parsed = parseSseLine(line)
    if (!parsed) continue
    if (parsed.event !== undefined) event = parsed.event
    if (parsed.data !== undefined) dataLines.push(parsed.data)
  }
  if (dataLines.length === 0 && event === 'message') return null
  const raw = dataLines.join('\n')
  let data: unknown = raw
  if (raw.length > 0) {
    try {
      data = JSON.parse(raw)
    } catch {
      // 非 JSON data（按 SSE 规范原样保留为字符串）
      data = raw
    }
  }
  return { event, data }
}

export async function streamChat(opts: StreamChatOptions): Promise<void> {
  const { query, threadId, signal } = opts

  let response: Response
  try {
    const token = localStorage.getItem('token')
    response = await fetch('/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ query, thread_id: threadId }),
      signal,
    })
  } catch (err) {
    opts.onError({ code: 'network_error', message: (err as Error).message })
    return
  }

  if (!response.ok || !response.body) {
    opts.onError({
      code: `http_${response.status}`,
      message: `HTTP ${response.status} ${response.statusText}`,
    })
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      // 归一化换行：后端 SSEEvent.format() 按 SSE 规范用 CRLF（\r\n\r\n 分隔事件），
      // 统一转成 \n 后前端才能按 "\n\n" 切分；同时兼容纯 LF / 纯 CR 的服务器。
      buffer += decoder
        .decode(value, { stream: true })
        .replace(/\r\n/g, '\n')
        .replace(/\r/g, '\n')

      // 切分完整事件
      let sepIdx = buffer.indexOf(EVENT_SEP)
      while (sepIdx !== -1) {
        const block = buffer.slice(0, sepIdx)
        buffer = buffer.slice(sepIdx + EVENT_SEP.length)
        if (block.trim().length > 0) {
          const evt = parseSseBlock(block)
          if (evt) dispatchEvent(evt, opts)
        }
        sepIdx = buffer.indexOf(EVENT_SEP)
      }
    }
    // flush 残余
    if (buffer.trim().length > 0) {
      const evt = parseSseBlock(buffer)
      if (evt) dispatchEvent(evt, opts)
    }
  } catch (err) {
    if ((err as { name?: string }).name === 'AbortError') {
      opts.onError({ code: 'aborted', message: 'request aborted' })
    } else {
      opts.onError({ code: 'stream_error', message: (err as Error).message })
    }
  }
}

function dispatchEvent(
  evt: { event: string; data: unknown },
  opts: StreamChatOptions
): void {
  const { event, data } = evt
  const { onStart, onStatus, onToken, onCitation, onCitationVerdict, onDone, onError, onWarning } =
    opts
  switch (event) {
    case 'start':
      onStart?.(data as StartEvent)
      break
    case 'status':
      onStatus?.(data as StatusEvent)
      break
    case 'token':
      onToken(data as TokenEvent)
      break
    case 'citation':
      onCitation(data as CitationEvent)
      break
    case 'citation_verdict':
      onCitationVerdict?.(data as CitationVerdictEvent)
      break
    case 'done':
      onDone(data as DoneEvent)
      break
    case 'error':
      onError(data as ErrorEvent)
      break
    case 'warning':
      onWarning?.(data as WarningEvent)
      break
    default:
      // 未知事件：忽略，不抛错（前后端可平滑扩展）
      break
  }
}

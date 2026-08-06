import { defineStore } from 'pinia'
import { ref } from 'vue'
import { streamChat } from '@/api/chat'
import { getThreads, getThreadMessages, deleteThread, type ThreadInfo } from '@/api/threads'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations?: any[]
  /** 后端返回的真实 message_id（assistant 消息在 SSE done 事件时填入） */
  messageId?: string
  /** 高风险提示文案（SSE warning 事件时填入） */
  warning?: string
  timestamp: Date
}

/** SSE 错误事件的最小结构（用于重连判定与最终展示） */
interface StreamError {
  code: string
  message: string
}

let _idCounter = 0
function nextId(): string {
  _idCounter += 1
  return `msg_${Date.now()}_${_idCounter}`
}

/** 后端 datetime('now') 存的是无时区的 UTC 时间，如 "2026-08-04 02:10:01"；
 *  需按 UTC 解析，否则 JS 会当本地时间处理，显示偏 8 小时。 */
function parseDbTime(s?: string): Date {
  if (!s) return new Date()
  return new Date(s.replace(' ', 'T') + 'Z')
}

function newThreadId(): string {
  return `t_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([])
  const threadId = ref<string>(newThreadId())
  const threads = ref<ThreadInfo[]>([])
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  function addMessage(message: Message) {
    messages.value.push(message)
  }

  function clearMessages() {
    messages.value = []
  }

  /** 加载会话列表 */
  async function loadThreads() {
    try {
      threads.value = await getThreads()
    } catch (e) {
      // 忽略：会话列表失败不影响当前对话
    }
  }

  /** 新建会话 */
  function newConversation() {
    threadId.value = newThreadId()
    clearMessages()
    error.value = null
  }

  /** 切换到某会话并加载其历史消息 */
  async function switchThread(id: string) {
    if (isLoading.value) return
    threadId.value = id
    clearMessages()
    try {
      const history = await getThreadMessages(id)
      messages.value = history.map((m) => ({
        id: nextId(),
        role: m.role === 'user' ? 'user' : 'assistant',
        content: m.content,
        // 恢复真实 message_id 与 citations，让历史助手消息也有反馈按钮和引用面板
        messageId: m.role === 'assistant' ? m.message_id : undefined,
        citations: m.citations?.length ? m.citations : undefined,
        timestamp: parseDbTime(m.created_at),
      }))
    } catch (e) {
      error.value = `加载会话失败: ${(e as any)?.message || e}`
    }
  }

  /** 删除会话 */
  async function removeThread(id: string) {
    await deleteThread(id)
    threads.value = threads.value.filter((t) => t.thread_id !== id)
    if (threadId.value === id) {
      newConversation()
    }
  }

  /** 上次发送的问题（用于"重试"） */
  const lastQuery = ref('')
  let activeController: AbortController | null = null

  // S3：流式中断自动重连——网络抖动/流中断在「未收到任何内容」时最多补试 2 次
  //（指数退避）。已产出内容不重连（避免半截回答拼接错乱）；用户停止/服务端
  // 错误（http_4xx/5xx、error 事件）不重试。
  const MAX_STREAM_ATTEMPTS = 3
  const RETRY_BACKOFF_MS = [1000, 3000]

  /** 发一条用户消息并流式消费 SSE 回答。 */
  async function sendMessage(query: string) {
    if (!query.trim() || isLoading.value) return
    lastQuery.value = query.trim()

    const userMsg: Message = {
      id: nextId(),
      role: 'user',
      content: query.trim(),
      timestamp: new Date(),
    }
    addMessage(userMsg)

    const assistantMsg: Message = {
      id: nextId(),
      role: 'assistant',
      content: '',
      citations: [],
      timestamp: new Date(),
    }
    addMessage(assistantMsg)

    isLoading.value = true
    error.value = null

    const assistantId = assistantMsg.id
    const controller = new AbortController()
    activeController = controller

    let finalError: StreamError | null = null
    let attempt = 0
    try {
      while (attempt < MAX_STREAM_ATTEMPTS) {
        attempt += 1
        // 每轮独立承载错误。用类型断言初始化而非字面量 null：TS 不追踪闭包内
        // 赋值，字面量 null 会被收窄并让循环内后续访问误判 never/不可达；
        // 断言保留联合类型，`=== null` 判定才能正确收窄。
        let attemptError: StreamError | null = null as StreamError | null
        let receivedToken = false
        let receivedDone = false

        await streamChat({
          query: query.trim(),
          threadId: threadId.value,
          signal: controller.signal,
          onToken: ({ delta }) => {
            receivedToken = true
            const m = messages.value.find((x) => x.id === assistantId)
            if (m) m.content += delta
          },
          onCitation: (citation) => {
            const m = messages.value.find((x) => x.id === assistantId)
            if (m) m.citations = [...(m.citations ?? []), citation]
          },
          onCitationVerdict: (e) => {
            // 答案生成后按 index 回填 verified，即时刷新当前会话的引用面板（G3.2）
            const m = messages.value.find((x) => x.id === assistantId)
            if (!m || !m.citations) return
            for (const item of e.items) {
              const c = m.citations[item.index - 1]
              if (c) c.verified = item.verified
            }
          },
          onDone: (e) => {
            receivedDone = true
            const m = messages.value.find((x) => x.id === assistantId)
            if (m && e.message_id) m.messageId = e.message_id
          },
          onWarning: (e) => {
            const m = messages.value.find((x) => x.id === assistantId)
            if (m && e.message) m.warning = e.message
          },
          onError: (e) => {
            attemptError = e
          },
        })

        // 已正常结束（done / 无错误静默关闭）→ 不再重试
        if (receivedDone || attemptError === null) break
        const retryable =
          !receivedToken &&
          attemptError.code !== 'aborted' &&
          !/^http_[45]\d\d$/.test(attemptError.code) &&
          attemptError.code !== 'error'
        if (!retryable || attempt >= MAX_STREAM_ATTEMPTS) {
          finalError = attemptError
          break
        }

        // 指数退避后重连：复用同一 assistant 消息继续追加，不新增消息
        await new Promise((r) => setTimeout(r, RETRY_BACKOFF_MS[attempt - 1]))
      }
    } finally {
      isLoading.value = false
      activeController = null
    }

    // 最终失败展示（重连成功/正常结束则 finalError 为 null）
    if (finalError) {
      if (finalError.code === 'aborted') {
        // 用户主动停止：不当作错误展示
        const m = messages.value.find((x) => x.id === assistantId)
        if (m && !m.content) m.content = '（已停止生成）'
      } else {
        error.value = finalError.message || '请求失败'
        const m = messages.value.find((x) => x.id === assistantId)
        if (m && !m.content) m.content = `（错误：${finalError.message || '请求失败'}）`
      }
    }

    // 发送后刷新会话列表（新会话会出现在顶部）
    await loadThreads()
  }

  /** 停止当前生成 */
  function stopGeneration() {
    activeController?.abort()
  }

  return {
    messages,
    threadId,
    threads,
    isLoading,
    error,
    lastQuery,
    addMessage,
    clearMessages,
    loadThreads,
    newConversation,
    switchThread,
    removeThread,
    sendMessage,
    stopGeneration,
  }
})

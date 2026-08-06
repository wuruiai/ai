import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Mock } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type {
  CitationEvent,
  DoneEvent,
  ErrorEvent,
  TokenEvent,
} from '@/api/chat'
import { streamChat } from '@/api/chat'
import { useChatStore } from '@/stores/chat'

vi.mock('@/api/chat', () => ({
  streamChat: vi.fn(),
}))

vi.mock('@/api/threads', () => ({
  getThreads: vi.fn().mockResolvedValue([]),
  getThreadMessages: vi.fn().mockResolvedValue([]),
  deleteThread: vi.fn().mockResolvedValue(undefined),
}))

const mockedStreamChat = streamChat as unknown as Mock

beforeEach(() => {
  setActivePinia(createPinia())
  mockedStreamChat.mockReset()
})

describe('chat store sendMessage', () => {
  it('流式拼接 token、累积引用、done 回填 messageId', async () => {
    mockedStreamChat.mockImplementation(async (opts: {
      onToken: (e: TokenEvent) => void
      onCitation: (e: CitationEvent) => void
      onDone: (e: DoneEvent) => void
    }) => {
      opts.onToken({ delta: '你' })
      opts.onToken({ delta: '好' })
      opts.onCitation({ index: 1, source_id: 's1' })
      opts.onDone({ message_id: 'm1' })
    })

    const store = useChatStore()
    await store.sendMessage('水利是什么？')

    expect(store.messages).toHaveLength(2)
    expect(store.messages[0].role).toBe('user')
    expect(store.messages[1].role).toBe('assistant')
    expect(store.messages[1].content).toBe('你好')
    expect(store.messages[1].citations).toHaveLength(1)
    expect(store.messages[1].messageId).toBe('m1')
    expect(store.isLoading).toBe(false)
  })

  it('错误分支：设置 error 并在空助手消息展示占位', async () => {
    mockedStreamChat.mockImplementation(async (opts: {
      onError: (e: ErrorEvent) => void
    }) => {
      opts.onError({ code: 'http_500', message: '服务器错误' })
    })

    const store = useChatStore()
    await store.sendMessage('q')
    expect(store.error).toBe('服务器错误')
    expect(store.messages[1].content).toBe('（错误：服务器错误）')
  })

  it('aborted 不当作错误展示', async () => {
    mockedStreamChat.mockImplementation(async (opts: {
      onError: (e: ErrorEvent) => void
    }) => {
      opts.onError({ code: 'aborted', message: 'request aborted' })
    })

    const store = useChatStore()
    await store.sendMessage('q')
    expect(store.error).toBeNull()
    expect(store.messages[1].content).toBe('（已停止生成）')
  })

  it('空白问题直接跳过，不调 streamChat', async () => {
    const store = useChatStore()
    await store.sendMessage('   ')
    expect(store.messages).toHaveLength(0)
    expect(mockedStreamChat).not.toHaveBeenCalled()
  })
})

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), delete: vi.fn() },
}))

import client from '@/api/client'
import { getThreads, deleteThread, getThreadMessages } from '@/api/threads'

const mockedClient = client as unknown as {
  get: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
}

describe('threads API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getThreads 返回会话列表，缺省空数组', async () => {
    mockedClient.get.mockResolvedValue({
      data: { threads: [{ thread_id: 't1', title: '会话一', message_count: 2 }] },
    })

    const res = await getThreads()

    expect(res).toHaveLength(1)
    expect(res[0].thread_id).toBe('t1')
    expect(mockedClient.get).toHaveBeenCalledWith('/threads/')
  })

  it('getThreads 响应无 threads 字段时回退空数组', async () => {
    mockedClient.get.mockResolvedValue({ data: {} })

    const res = await getThreads()

    expect(res).toEqual([])
  })

  it('deleteThread 按 id 删除会话', async () => {
    mockedClient.delete.mockResolvedValue({ data: { ok: true } })

    const res = await deleteThread('t1')

    expect(res).toEqual({ ok: true })
    expect(mockedClient.delete).toHaveBeenCalledWith('/threads/t1')
  })

  it('getThreadMessages 拉取会话消息', async () => {
    mockedClient.get.mockResolvedValue({
      data: { messages: [{ message_id: 'm1', role: 'user', content: 'hi' }] },
    })

    const res = await getThreadMessages('t1')

    expect(res).toHaveLength(1)
    expect(res[0].content).toBe('hi')
    expect(mockedClient.get).toHaveBeenCalledWith('/threads/t1/messages')
  })
})

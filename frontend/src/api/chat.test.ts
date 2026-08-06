import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'
import { parseSseBlock, parseSseLine, streamChat } from '@/api/chat'

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  register: vi.fn(),
  refresh: vi.fn().mockResolvedValue({
    access_token: 'new-token',
    refresh_token: 'new-refresh',
  }),
  logout: vi.fn(),
}))

describe('parseSseLine', () => {
  it('解析 event 行', () => {
    expect(parseSseLine('event: token')).toEqual({ event: 'token' })
  })

  it('解析 data 行（剥掉一个前导空格）', () => {
    expect(parseSseLine('data: {"delta":"水"}')).toEqual({ data: '{"delta":"水"}' })
  })

  it('忽略注释行 / 空行 / 无冒号行', () => {
    expect(parseSseLine(': keep-alive')).toBeNull()
    expect(parseSseLine('')).toBeNull()
    expect(parseSseLine('   ')).toBeNull()
    expect(parseSseLine('plain-text')).toBeNull()
  })
})

describe('parseSseBlock', () => {
  it('聚合 event + data 并 JSON.parse', () => {
    const evt = parseSseBlock('event: token\ndata: {"delta":"水"}')
    expect(evt).toEqual({ event: 'token', data: { delta: '水' } })
  })

  it('无 event 时默认 message，data 为数字类型', () => {
    expect(parseSseBlock('data: 1')).toEqual({ event: 'message', data: 1 })
  })

  it('data 非 JSON 时原样保留为字符串', () => {
    expect(parseSseBlock('event: status\ndata: ok')).toEqual({
      event: 'status',
      data: 'ok',
    })
  })

  it('纯 event 无 data：保留 event 且 data 为空串', () => {
    expect(parseSseBlock('event: keepalive')).toEqual({ event: 'keepalive', data: '' })
  })

  it('注释块返回 null', () => {
    expect(parseSseBlock(': heartbeat')).toBeNull()
  })
})

describe('streamChat 401 刷新重放', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('401 时刷新 token 并带新 token 重放一次', async () => {
    const store = useAuthStore()
    store.setAuth('old-token', 'refresh-token', {
      user_id: 'u1',
      username: 'u',
      role: 'user',
    })

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('', { status: 401 }))
      .mockResolvedValueOnce(
        new Response('event: done\ndata: {"message_id":"m1"}\n\n', { status: 200 })
      )
    vi.stubGlobal('fetch', fetchMock)

    const onDone = vi.fn()
    await streamChat({
      query: 'q',
      threadId: 't',
      onToken: vi.fn(),
      onCitation: vi.fn(),
      onDone,
      onError: vi.fn(),
    })

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(store.accessToken).toBe('new-token')
    // 第二次 POST 携带刷新后的新 token
    const secondCall = fetchMock.mock.calls[1]
    expect(secondCall[1].headers.Authorization).toBe('Bearer new-token')
    expect(onDone).toHaveBeenCalledWith({ message_id: 'm1' })
  })

  it('refresh 失败时保留原 401 交给 onError，不无限重放', async () => {
    const store = useAuthStore()
    store.setAuth('old-token', 'refresh-token', {
      user_id: 'u1',
      username: 'u',
      role: 'user',
    })

    const authApi = await import('@/api/auth')
    ;(authApi.refresh as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('refresh expired')
    )

    const fetchMock = vi.fn().mockResolvedValueOnce(new Response('', { status: 401 }))
    vi.stubGlobal('fetch', fetchMock)

    const onError = vi.fn()
    await streamChat({
      query: 'q',
      threadId: 't',
      onToken: vi.fn(),
      onCitation: vi.fn(),
      onDone: vi.fn(),
      onError,
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ code: 'http_401' }))
  })
})

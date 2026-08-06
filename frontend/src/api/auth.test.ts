import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import client from '@/api/client'
import { register, login, refresh, logout, me } from '@/api/auth'

const mockedClient = client as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
}

const AUTH_RESP = {
  access_token: 'at',
  refresh_token: 'rt',
  token_type: 'bearer',
  expires_in: 3600,
  user: { user_id: 'u1', username: 'alice', role: 'user' },
}

describe('auth API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('register 提交注册（displayName 缺省不发送字段）', async () => {
    mockedClient.post.mockResolvedValue({ data: AUTH_RESP })

    const res = await register('alice', 'secret')

    expect(res.access_token).toBe('at')
    expect(mockedClient.post).toHaveBeenCalledWith('/auth/register', {
      username: 'alice',
      password: 'secret',
      display_name: undefined,
    })
  })

  it('register 携带显示名称', async () => {
    mockedClient.post.mockResolvedValue({ data: AUTH_RESP })

    await register('alice', 'secret', '爱丽丝')

    expect(mockedClient.post).toHaveBeenCalledWith('/auth/register', {
      username: 'alice',
      password: 'secret',
      display_name: '爱丽丝',
    })
  })

  it('login 提交登录', async () => {
    mockedClient.post.mockResolvedValue({ data: AUTH_RESP })

    await login('alice', 'secret')

    expect(mockedClient.post).toHaveBeenCalledWith('/auth/login', {
      username: 'alice',
      password: 'secret',
    })
  })

  it('refresh 用 refresh token 换新对', async () => {
    mockedClient.post.mockResolvedValue({ data: AUTH_RESP })

    await refresh('rt-old')

    expect(mockedClient.post).toHaveBeenCalledWith('/auth/refresh', {
      refresh_token: 'rt-old',
    })
  })

  it('logout 默认只吊销本设备，all=true 吊销全部', async () => {
    mockedClient.post.mockResolvedValue({ data: {} })

    await logout('rt')
    expect(mockedClient.post).toHaveBeenLastCalledWith('/auth/logout', {
      refresh_token: 'rt',
      all: false,
    })

    await logout('rt', true)
    expect(mockedClient.post).toHaveBeenLastCalledWith('/auth/logout', {
      refresh_token: 'rt',
      all: true,
    })
  })

  it('me 拉取当前用户', async () => {
    mockedClient.get.mockResolvedValue({ data: AUTH_RESP.user })

    const res = await me()

    expect(res.username).toBe('alice')
    expect(mockedClient.get).toHaveBeenCalledWith('/auth/me')
  })
})

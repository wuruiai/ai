import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import * as authApi from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  register: vi.fn(),
  refresh: vi.fn(),
  logout: vi.fn(),
}))

const mockedLogin = authApi.login as unknown as ReturnType<typeof vi.fn>
const mockedLogout = authApi.logout as unknown as ReturnType<typeof vi.fn>

const adminUser = { user_id: 'u1', username: 'admin', role: 'admin' }

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  mockedLogin.mockReset()
  mockedLogout.mockReset()
})

describe('auth store', () => {
  it('setAuth 持久化 token 与用户到 localStorage', () => {
    const store = useAuthStore()
    store.setAuth('at', 'rt', adminUser)
    expect(localStorage.getItem('access_token')).toBe('at')
    expect(localStorage.getItem('refresh_token')).toBe('rt')
    expect(localStorage.getItem('user')).toContain('admin')
    expect(store.isAuthenticated).toBe(true)
    expect(store.isAdmin).toBe(true)
    expect(store.token).toBe('at')
  })

  it('clearToken 清空会话且不再认证', () => {
    const store = useAuthStore()
    store.setAuth('at', 'rt', adminUser)
    store.clearToken()
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
    expect(store.isAuthenticated).toBe(false)
  })

  it('login 调用 api 并写入 store', async () => {
    mockedLogin.mockResolvedValue({
      access_token: 'at',
      refresh_token: 'rt',
      user: adminUser,
    })
    const store = useAuthStore()
    const user = await store.login('admin', 'secret')
    expect(authApi.login).toHaveBeenCalledWith('admin', 'secret')
    expect(user.username).toBe('admin')
    expect(store.accessToken).toBe('at')
    expect(localStorage.getItem('access_token')).toBe('at')
  })

  it('logout 清空本地并尽力吊销 refresh', async () => {
    mockedLogout.mockResolvedValue(undefined)
    const store = useAuthStore()
    store.setAuth('at', 'rt', adminUser)
    await store.logout()
    expect(store.isAuthenticated).toBe(false)
    expect(localStorage.getItem('refresh_token')).toBeNull()
    expect(authApi.logout).toHaveBeenCalledWith('rt')
  })

  it('旧版 token key 首次加载时迁移到 access_token', () => {
    localStorage.setItem('token', 'legacy-token')
    const store = useAuthStore()
    expect(store.accessToken).toBe('legacy-token')
    expect(localStorage.getItem('access_token')).toBe('legacy-token')
    expect(localStorage.getItem('token')).toBeNull()
  })
})

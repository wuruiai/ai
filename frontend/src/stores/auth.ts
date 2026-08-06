import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as authApi from '@/api/auth'

export interface AuthUser {
  user_id: string
  username: string
  role: string
  display_name?: string
}

const ACCESS_KEY = 'access_token'
const REFRESH_KEY = 'refresh_token'
const USER_KEY = 'user'

export const useAuthStore = defineStore('auth', () => {
  // 旧版本只持久化了 token（= access token）。首次加载时迁移到新 key。
  if (!localStorage.getItem(ACCESS_KEY) && localStorage.getItem('token')) {
    localStorage.setItem(ACCESS_KEY, localStorage.getItem('token') as string)
    localStorage.removeItem('token')
  }

  const accessToken = ref<string | null>(localStorage.getItem(ACCESS_KEY))
  const refreshToken = ref<string | null>(localStorage.getItem(REFRESH_KEY))
  const user = ref<AuthUser | null>(
    (() => {
      try {
        return JSON.parse(localStorage.getItem(USER_KEY) || 'null')
      } catch {
        return null
      }
    })()
  )

  const isAdmin = computed(() => user.value?.role === 'admin')
  const isAuthenticated = computed(() => !!accessToken.value)
  // 兼容旧组件引用：token === accessToken
  const token = computed(() => accessToken.value)

  // 单飞 refresh：并发 401 只发一次 refresh（轮换会吊销旧 refresh，重复调用必失败）
  let refreshPromise: Promise<string> | null = null

  function persist() {
    if (accessToken.value) localStorage.setItem(ACCESS_KEY, accessToken.value)
    else localStorage.removeItem(ACCESS_KEY)
    if (refreshToken.value) localStorage.setItem(REFRESH_KEY, refreshToken.value)
    else localStorage.removeItem(REFRESH_KEY)
    if (user.value) localStorage.setItem(USER_KEY, JSON.stringify(user.value))
    else localStorage.removeItem(USER_KEY)
  }

  function setAuth(t: string, r: string, u: AuthUser) {
    accessToken.value = t
    refreshToken.value = r
    user.value = u
    persist()
  }

  function clearToken() {
    accessToken.value = null
    refreshToken.value = null
    user.value = null
    refreshPromise = null
    persist()
  }

  /** 换取新 token 对；失败抛出（由 client.ts 拦截器统一兜底跳登录）。 */
  async function refresh(): Promise<string> {
    if (refreshPromise) return refreshPromise
    if (!refreshToken.value) throw new Error('no refresh token')
    const rt = refreshToken.value
    refreshPromise = authApi
      .refresh(rt)
      .then((res) => {
        accessToken.value = res.access_token
        refreshToken.value = res.refresh_token
        if (res.user) user.value = res.user
        persist()
        return res.access_token
      })
      .finally(() => {
        refreshPromise = null
      })
    return refreshPromise
  }

  async function login(username: string, password: string) {
    const res = await authApi.login(username, password)
    setAuth(res.access_token, res.refresh_token, res.user)
    return res.user
  }

  async function register(username: string, password: string, displayName?: string) {
    const res = await authApi.register(username, password, displayName)
    setAuth(res.access_token, res.refresh_token, res.user)
    return res.user
  }

  /** 登出：先清本地，再尽力吊销服务端 refresh（离线/失败也照常登出）。 */
  async function logout() {
    const rt = refreshToken.value
    clearToken()
    if (rt) {
      try {
        await authApi.logout(rt)
      } catch {
        // 服务端吊销失败（网络异常/会话已过期）不影响本地登出
      }
    }
  }

  return {
    token,
    accessToken,
    refreshToken,
    user,
    isAdmin,
    isAuthenticated,
    login,
    register,
    logout,
    refresh,
    clearToken,
    setAuth,
  }
})

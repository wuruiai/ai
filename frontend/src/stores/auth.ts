import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as authApi from '@/api/auth'

export interface AuthUser {
  user_id: string
  username: string
  role: string
  display_name?: string
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem('token'))
  const user = ref<AuthUser | null>(
    (() => {
      try {
        return JSON.parse(localStorage.getItem('user') || 'null')
      } catch {
        return null
      }
    })()
  )

  const isAdmin = computed(() => user.value?.role === 'admin')
  const isAuthenticated = computed(() => !!token.value)

  function persist() {
    if (token.value) localStorage.setItem('token', token.value)
    else localStorage.removeItem('token')
    if (user.value) localStorage.setItem('user', JSON.stringify(user.value))
    else localStorage.removeItem('user')
  }

  function setAuth(t: string, u: AuthUser) {
    token.value = t
    user.value = u
    persist()
  }

  function clearToken() {
    token.value = null
    user.value = null
    persist()
  }

  async function login(username: string, password: string) {
    const res = await authApi.login(username, password)
    setAuth(res.token, res.user)
    return res.user
  }

  async function register(username: string, password: string, displayName?: string) {
    const res = await authApi.register(username, password, displayName)
    setAuth(res.token, res.user)
    return res.user
  }

  function logout() {
    clearToken()
  }

  return {
    token,
    user,
    isAdmin,
    isAuthenticated,
    login,
    register,
    logout,
    clearToken,
    setAuth,
  }
})

import axios from 'axios'
import { useAuthStore } from '@/stores/auth'

declare module 'axios' {
  interface InternalAxiosRequestConfig {
    /** 标记已 refresh 重放过一次的请求，防止 401 死循环 */
    _retry?: boolean
  }
}

const client = axios.create({
  baseURL: '/api/v1',
  timeout: 60000,
})

// 请求拦截器：自动附带 access token
client.interceptors.request.use((config) => {
  const authStore = useAuthStore()
  if (authStore.accessToken) {
    config.headers.Authorization = `Bearer ${authStore.accessToken}`
  }
  return config
})

// 响应拦截器：access token 过期（401）时用 refresh token 换新后自动重放一次
client.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config
    const status = error.response?.status
    const isAuthEndpoint = typeof original?.url === 'string' && original.url.startsWith('/auth/')

    // 认证接口自身的 401（登录失败 / refresh 过期）不做 refresh 兜底，避免死循环
    if (status === 401 && !isAuthEndpoint && original && !original._retry) {
      const authStore = useAuthStore()
      if (authStore.refreshToken) {
        try {
          const newAccess = await authStore.refresh()
          original._retry = true
          original.headers.Authorization = `Bearer ${newAccess}`
          return client(original)
        } catch {
          // refresh 失败（过期/被吊销/离线）→ 清空会话回登录页
          authStore.clearToken()
          if (window.location.pathname !== '/login') {
            window.location.replace('/login')
          }
        }
      } else {
        // 无 refresh token（本地已无会话）→ 直接回登录页
        authStore.clearToken()
        if (window.location.pathname !== '/login') {
          window.location.replace('/login')
        }
      }
    }
    return Promise.reject(error)
  }
)

export default client

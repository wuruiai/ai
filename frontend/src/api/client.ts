import axios from 'axios'
import { useAuthStore } from '@/stores/auth'

const client = axios.create({
  baseURL: '/api/v1',
  timeout: 60000,
})

// 请求拦截器
client.interceptors.request.use((config) => {
  const authStore = useAuthStore()
  if (authStore.token) {
    config.headers.Authorization = `Bearer ${authStore.token}`
  }
  return config
})

// 响应拦截器
client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const authStore = useAuthStore()
      authStore.clearToken()
    }
    return Promise.reject(error)
  }
)

export default client

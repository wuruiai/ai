import { useAuthStore } from '@/stores/auth'

/**
 * 统一取当前 access token：SSE（fetch）与 axios 同源，都走 auth store，
 * 消除 localStorage 直读造成的双通道漂移（G6.2）。
 *
 * 注意：必须在 Pinia 激活后调用（组件 / 路由守卫 / 拦截器运行时），
 * 不要在模块顶层调用。未登录返回 null。
 */
export function getToken(): string | null {
  return useAuthStore().accessToken
}

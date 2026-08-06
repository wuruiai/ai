import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { getHealth } from '@/api/system'
import App from '@/App.vue'

vi.mock('vue-router', () => ({
  useRoute: () => ({ path: '/' }), // 非登录页 → 渲染主布局（含侧边栏）
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({
    isAdmin: false,
    user: { username: 'admin', role: 'user' },
    logout: vi.fn(),
  }),
}))

vi.mock('@/components/BoundaryRouterView.vue', () => ({
  default: { template: '<div class="router-view-stub" />' },
}))

vi.mock('@/api/system', () => ({
  getHealth: vi.fn(),
}))

const mockedGetHealth = getHealth as unknown as ReturnType<typeof vi.fn>

describe('App 版本对齐（G10.9 M12）', () => {
  beforeEach(() => {
    mockedGetHealth.mockReset()
  })

  it('侧边栏品牌区展示后端版本（单一来源 /health）', async () => {
    mockedGetHealth.mockResolvedValue({ status: 'ok', version: '1.1.0', schema_version: 1 })

    const wrapper = mount(App)
    await flushPromises()

    expect(getHealth).toHaveBeenCalled()
    expect(wrapper.find('.brand-version').text()).toBe('v1.1.0')
  })

  it('后端不可达时隐藏版本徽标，不阻塞页面渲染', async () => {
    mockedGetHealth.mockRejectedValue(new Error('network'))

    const wrapper = mount(App)
    await flushPromises()

    expect(wrapper.find('.brand-version').exists()).toBe(false)
  })
})

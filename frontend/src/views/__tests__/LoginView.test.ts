import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import * as authApi from '@/api/auth'
import LoginView from '@/views/LoginView.vue'

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushMock }),
}))

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  register: vi.fn(),
  refresh: vi.fn(),
  logout: vi.fn(),
}))

const mockedLogin = authApi.login as unknown as ReturnType<typeof vi.fn>

let pinia: ReturnType<typeof createPinia>

beforeEach(() => {
  localStorage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
  pushMock.mockReset()
  mockedLogin.mockReset()
})

describe('LoginView', () => {
  it('登录表单提交：调 login 并跳转首页', async () => {
    mockedLogin.mockResolvedValue({
      access_token: 'at',
      refresh_token: 'rt',
      user: { user_id: 'u1', username: 'admin', role: 'admin' },
    })

    const wrapper = mount(LoginView, { global: { plugins: [pinia] } })
    await wrapper.find('input[placeholder*="字母"]').setValue('admin')
    await wrapper.find('input[type="password"]').setValue('secret')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(authApi.login).toHaveBeenCalledWith('admin', 'secret')
    expect(pushMock).toHaveBeenCalledWith('/')
    // 成功后无错误提示
    expect(wrapper.find('.login-error').exists()).toBe(false)
  })

  it('登录失败：展示可读错误信息', async () => {
    mockedLogin.mockRejectedValue({
      response: { data: { detail: '用户名或密码错误' } },
    })

    const wrapper = mount(LoginView, { global: { plugins: [pinia] } })
    await wrapper.find('input[placeholder*="字母"]').setValue('admin')
    await wrapper.find('input[type="password"]').setValue('wrong')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('.login-error').text()).toContain('用户名或密码错误')
    expect(pushMock).not.toHaveBeenCalled()
  })
})

import { describe, expect, it } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import ErrorBoundary from '@/components/ErrorBoundary.vue'

// 渲染即抛错的子组件，模拟路由页面渲染异常
const BoomView = defineComponent({
  setup() {
    throw new Error('boom in render')
  },
})

describe('ErrorBoundary', () => {
  it('子组件渲染抛错时显示降级卡片而非白屏', async () => {
    const wrapper = mount(ErrorBoundary, {
      slots: { default: () => h(BoomView) },
    })
    await nextTick()

    expect(wrapper.find('.eb-card').exists()).toBe(true)
    expect(wrapper.text()).toContain('页面渲染异常')
    expect(wrapper.text()).toContain('boom in render')
  })

  it('正常内容原样透传', () => {
    const wrapper = mount(ErrorBoundary, {
      slots: { default: '<p class="ok-content">正常内容</p>' },
    })
    expect(wrapper.find('.ok-content').exists()).toBe(true)
    expect(wrapper.find('.eb-card').exists()).toBe(false)
  })
})

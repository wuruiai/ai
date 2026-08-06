import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import CitationPanel from '@/components/CitationPanel.vue'

describe('CitationPanel', () => {
  it('渲染来源名称、引用片段与核实徽章', () => {
    const wrapper = mount(CitationPanel, {
      props: {
        citations: [
          { source_name: '防洪规范', content: '堤防设计标准', verified: true },
          { source_name: '水库调度', content: '汛限水位', verified: false },
          { content: '仅片段无来源名' },
        ],
      },
    })

    const text = wrapper.text()
    expect(text).toContain('防洪规范')
    expect(text).toContain('已核实')
    expect(text).toContain('水库调度')
    expect(text).toContain('待核实')
    // 缺 source_name 时回退「未知来源」
    expect(text).toContain('未知来源')
    expect(text).toContain('仅片段无来源名')
  })

  it('空引用列表渲染空面板', () => {
    const wrapper = mount(CitationPanel, { props: { citations: [] } })
    expect(wrapper.text()).toContain('来源引用')
    expect(wrapper.find('.citation-item').exists()).toBe(false)
  })
})

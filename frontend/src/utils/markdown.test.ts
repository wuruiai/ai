import { describe, expect, it } from 'vitest'
import { renderMarkdown } from './markdown'

describe('renderMarkdown（XSS 安全渲染）', () => {
  it('渲染标准 Markdown 语法', () => {
    const html = renderMarkdown('# 标题\n\n段落内容')
    expect(html).toContain('<h1>标题</h1>')
    expect(html).toContain('<p>段落内容</p>')
  })

  it('html:false——原始 HTML 被转义而非执行（防 XSS）', () => {
    const html = renderMarkdown('<script>alert(1)</script>')
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;script&gt;')
  })

  it('linkify 把裸 URL 转成链接', () => {
    const html = renderMarkdown('访问 https://example.com 查看')
    expect(html).toContain('<a href="https://example.com"')
  })
})

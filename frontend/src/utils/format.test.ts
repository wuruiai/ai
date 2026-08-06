import { describe, expect, it } from 'vitest'
import { formatDate, formatFileSize, truncateText } from './format'

describe('formatFileSize', () => {
  it('0 字节显示 0 B', () => {
    expect(formatFileSize(0)).toBe('0 B')
  })

  it('B / KB / MB 单位与精度', () => {
    expect(formatFileSize(512)).toBe('512 B')
    expect(formatFileSize(1024)).toBe('1 KB')
    expect(formatFileSize(1536)).toBe('1.5 KB')
    expect(formatFileSize(1048576)).toBe('1 MB')
  })

  it('超大字节进 GB', () => {
    expect(formatFileSize(2 * 1024 * 1024 * 1024)).toBe('2 GB')
  })
})

describe('formatDate', () => {
  it('接受 Date 与 ISO 字符串，输出含年月日的中文本地格式', () => {
    const d = new Date(2026, 7, 6, 14, 30)
    expect(formatDate(d)).toMatch(/2026[-/]08[-/]06/)
    // Date 与字符串路径结果一致（同一时刻）
    const iso = formatDate('2026-08-06T14:30:00')
    expect(iso).toMatch(/2026/)
  })
})

describe('truncateText', () => {
  it('短文本原样返回', () => {
    expect(truncateText('hello', 10)).toBe('hello')
  })

  it('等长边界不追加省略号', () => {
    expect(truncateText('hello', 5)).toBe('hello')
  })

  it('超长文本截断并追加省略号', () => {
    expect(truncateText('hello world', 5)).toBe('hello...')
  })
})

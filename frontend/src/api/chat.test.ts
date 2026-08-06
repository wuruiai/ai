import { describe, expect, it } from 'vitest'
import { parseSseBlock, parseSseLine } from '@/api/chat'

describe('parseSseLine', () => {
  it('解析 event 行', () => {
    expect(parseSseLine('event: token')).toEqual({ event: 'token' })
  })

  it('解析 data 行（剥掉一个前导空格）', () => {
    expect(parseSseLine('data: {"delta":"水"}')).toEqual({ data: '{"delta":"水"}' })
  })

  it('忽略注释行 / 空行 / 无冒号行', () => {
    expect(parseSseLine(': keep-alive')).toBeNull()
    expect(parseSseLine('')).toBeNull()
    expect(parseSseLine('   ')).toBeNull()
    expect(parseSseLine('plain-text')).toBeNull()
  })
})

describe('parseSseBlock', () => {
  it('聚合 event + data 并 JSON.parse', () => {
    const evt = parseSseBlock('event: token\ndata: {"delta":"水"}')
    expect(evt).toEqual({ event: 'token', data: { delta: '水' } })
  })

  it('无 event 时默认 message，data 为数字类型', () => {
    expect(parseSseBlock('data: 1')).toEqual({ event: 'message', data: 1 })
  })

  it('data 非 JSON 时原样保留为字符串', () => {
    expect(parseSseBlock('event: status\ndata: ok')).toEqual({
      event: 'status',
      data: 'ok',
    })
  })

  it('纯 event 无 data：保留 event 且 data 为空串', () => {
    expect(parseSseBlock('event: keepalive')).toEqual({ event: 'keepalive', data: '' })
  })

  it('注释块返回 null', () => {
    expect(parseSseBlock(': heartbeat')).toBeNull()
  })
})

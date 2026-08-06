import { describe, expect, it } from 'vitest'
import { extractError } from './error'

describe('extractError', () => {
  it('统一 envelope 格式优先（error.message）', () => {
    expect(
      extractError({ response: { data: { error: { message: '预算不足' } } } })
    ).toBe('预算不足')
  })

  it('旧 detail 格式', () => {
    expect(extractError({ response: { data: { detail: '上传失败' } } })).toBe(
      '上传失败'
    )
  })

  it('网络层错误取 e.message', () => {
    expect(extractError(new Error('Network Error'))).toBe('Network Error')
  })

  it('无任何信息时回退默认文案', () => {
    expect(extractError({})).toBe('请求失败')
    expect(extractError(undefined, '自定义兜底')).toBe('自定义兜底')
  })

  it('优先级：envelope > detail > message', () => {
    expect(
      extractError({
        message: '底层信息',
        response: { data: { detail: 'detail 信息' } },
      })
    ).toBe('detail 信息')
  })
})

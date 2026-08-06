import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/client', () => ({
  default: { post: vi.fn() },
}))

import client from '@/api/client'
import { submitFeedback } from '@/api/feedback'

const mockedPost = client.post as ReturnType<typeof vi.fn>

describe('feedback API', () => {
  beforeEach(() => {
    mockedPost.mockReset()
  })

  it('提交反馈：comment 缺省为空串', async () => {
    mockedPost.mockResolvedValue({ data: { ok: true } })

    const res = await submitFeedback('msg-1', 'helpful')

    expect(res).toEqual({ ok: true })
    expect(mockedPost).toHaveBeenCalledWith('/feedback/', {
      message_id: 'msg-1',
      rating: 'helpful',
      comment: '',
    })
  })

  it('提交反馈：携带 comment', async () => {
    mockedPost.mockResolvedValue({ data: { ok: true } })

    await submitFeedback('msg-1', 'not_helpful', '回答有误')

    expect(mockedPost).toHaveBeenCalledWith('/feedback/', {
      message_id: 'msg-1',
      rating: 'not_helpful',
      comment: '回答有误',
    })
  })
})

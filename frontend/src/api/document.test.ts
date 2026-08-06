import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn(), patch: vi.fn() },
}))

import client from '@/api/client'
import {
  getDocuments,
  uploadDocument,
  deleteDocument,
  updateDocument,
} from '@/api/document'

const mockedClient = client as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
  patch: ReturnType<typeof vi.fn>
}

describe('document API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('uploadDocument 用 FormData 提交且不手动设 Content-Type（M14 回归）', async () => {
    mockedClient.post.mockResolvedValue({ data: { document_id: 'd1' } })
    const file = new File(['abc'], '测试.pdf', { type: 'application/pdf' })

    const res = await uploadDocument(file)

    expect(res).toEqual({ document_id: 'd1' })
    expect(mockedClient.post).toHaveBeenCalledTimes(1)
    const [url, formData, headers] = mockedClient.post.mock.calls[0]
    expect(url).toBe('/documents/')
    expect(formData).toBeInstanceOf(FormData)
    // M14：手动设 `multipart/form-data` 会丢失 boundary，axios 传 FormData
    // 必须由浏览器自动生成带 boundary 的头——post 只允许 2 个参数（无 headers）
    expect(headers).toBeUndefined()
    expect((formData as FormData).get('file')).toBe(file)
  })

  it('getDocuments 拉取文档列表', async () => {
    mockedClient.get.mockResolvedValue({
      data: { documents: [{ document_id: 'd1' }] },
    })

    const res = await getDocuments()

    expect(res).toEqual({ documents: [{ document_id: 'd1' }] })
    expect(mockedClient.get).toHaveBeenCalledWith('/documents/')
  })

  it('deleteDocument 按 id 删除', async () => {
    mockedClient.delete.mockResolvedValue({ data: { ok: true } })

    const res = await deleteDocument('doc-1')

    expect(res).toEqual({ ok: true })
    expect(mockedClient.delete).toHaveBeenCalledWith('/documents/doc-1')
  })

  it('updateDocument PATCH 元数据（分类/标签/启用）', async () => {
    mockedClient.patch.mockResolvedValue({ data: { document_id: 'doc-1' } })
    const input = { category: '防洪', tags: 'a,b', is_enabled: 0 }

    const res = await updateDocument('doc-1', input)

    expect(res).toEqual({ document_id: 'doc-1' })
    expect(mockedClient.patch).toHaveBeenCalledWith('/documents/doc-1', input)
  })
})

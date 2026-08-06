import client from './client'

export async function getDocuments() {
  const response = await client.get('/documents/')
  return response.data
}

export async function uploadDocument(file: File) {
  const formData = new FormData()
  formData.append('file', file)
  // M14：不能手动设 Content-Type——axios 传 FormData 时需由浏览器自动生成
  // 带 boundary 的 multipart 头；手动设 `multipart/form-data` 会丢失 boundary，
  // FastAPI 无法解析文件字段（500/422）。
  const response = await client.post('/documents/', formData)
  return response.data
}

export async function getDocument(id: string) {
  const response = await client.get(`/documents/${id}`)
  return response.data
}

export async function deleteDocument(id: string) {
  const response = await client.delete(`/documents/${id}`)
  return response.data
}

export interface DocumentUpdateInput {
  category?: string | null
  tags?: string | null
  is_enabled?: number
}

export async function updateDocument(id: string, input: DocumentUpdateInput) {
  const response = await client.patch(`/documents/${id}`, input)
  return response.data
}

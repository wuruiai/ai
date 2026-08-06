import client from './client'

export async function getDocuments() {
  const response = await client.get('/documents/')
  return response.data
}

export async function uploadDocument(file: File) {
  const formData = new FormData()
  formData.append('file', file)
  const response = await client.post('/documents/', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  })
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

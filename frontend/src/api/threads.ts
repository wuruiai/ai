import client from './client'

export interface ThreadInfo {
  thread_id: string
  title: string
  message_count: number
  created_at?: string
  updated_at?: string
}

export async function getThreads(): Promise<ThreadInfo[]> {
  const response = await client.get('/threads/')
  return response.data.threads || []
}

export async function deleteThread(threadId: string) {
  const response = await client.delete(`/threads/${threadId}`)
  return response.data
}

export interface ThreadMessage {
  message_id: string
  role: 'user' | 'assistant'
  content: string
  citations?: any[]
  created_at?: string
}

export async function getThreadMessages(threadId: string): Promise<ThreadMessage[]> {
  const response = await client.get(`/threads/${threadId}/messages`)
  return response.data.messages || []
}

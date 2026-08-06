import client from './client'

export async function submitFeedback(messageId: string, rating: string, comment?: string) {
  const response = await client.post('/feedback/', {
    message_id: messageId,
    rating,
    comment: comment || '',
  })
  return response.data
}

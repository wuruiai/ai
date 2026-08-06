import { defineStore } from 'pinia'
import { ref } from 'vue'

interface Document {
  document_id: string
  file_name: string
  document_title: string
  file_size: number
  status: string
  chunk_count: number
  error_msg?: string | null
  created_at: string
  updated_at: string
}

export const useDocumentStore = defineStore('document', () => {
  const documents = ref<Document[]>([])
  const isLoading = ref(false)

  function setDocuments(docs: Document[]) {
    documents.value = docs
  }

  function addDocument(doc: Document) {
    documents.value.push(doc)
  }

  function removeDocument(id: string) {
    documents.value = documents.value.filter(d => d.document_id !== id)
  }

  return {
    documents,
    isLoading,
    setDocuments,
    addDocument,
    removeDocument,
  }
})

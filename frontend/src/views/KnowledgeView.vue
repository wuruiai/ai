<template>
  <div class="knowledge-view">
    <div class="page-header">
      <h1>知识库管理</h1>
      <p class="subtitle">上传 PDF / DOCX / TXT / Markdown 文档，系统自动解析并建立索引</p>
    </div>

    <div class="upload-area">
      <DocumentUploader @uploaded="onUploaded" />
    </div>

    <div v-if="categories.length" class="filter-row">
      <label class="filter-label">分类：</label>
      <select v-model="categoryFilter" class="filter-select" aria-label="按分类筛选" @change="loadDocuments">
        <option value="">全部</option>
        <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
      </select>
    </div>

    <div v-if="loadError" class="load-error">⚠️ {{ loadError }}</div>

    <div v-if="!documentStore.documents.length && !loading" class="empty-state">
      <div class="empty-icon">📚</div>
      <p>知识库为空，上传第一份文档开始使用</p>
    </div>

    <div v-else class="document-list">
      <div
        v-for="doc in filteredDocs"
        :key="doc.document_id"
        class="document-card"
      >
        <div class="doc-icon">📄</div>
        <div class="doc-info">
          <div class="doc-title">{{ doc.file_name }}</div>
          <div class="doc-meta">
            <DocumentStatusBadge :status="doc.status" />
            <span v-if="doc.category" class="cat-tag">{{ doc.category }}</span>
            <span v-if="doc.tags" class="tags-text" :title="doc.tags">{{ doc.tags }}</span>
            <span v-if="doc.is_enabled === 0" class="disabled-tag">已禁用</span>
            <span class="meta-item">{{ doc.file_size }} 字节</span>
            <span class="meta-item">{{ doc.chunk_count }} 片段</span>
            <span class="meta-item">{{ formatTime(doc.created_at) }}</span>
          </div>
        </div>
        <div class="doc-actions">
          <router-link :to="`/document/${doc.document_id}`" class="action-link">详情</router-link>
          <button type="button" @click="toggleEdit(doc)" class="edit-btn">管理</button>
          <button type="button" @click="deleteDoc(doc.document_id)" class="delete-btn">删除</button>
        </div>

        <!-- 知识库管理：分类 / 标签 / 启用 -->
        <div v-if="editingId === doc.document_id" class="doc-edit">
          <input v-model="editCategory" class="edit-input" aria-label="文档分类" placeholder="分类（如：防洪 / 灌溉 / 规范）" maxlength="64" />
          <input v-model="editTags" class="edit-input" aria-label="文档标签" placeholder="标签（逗号分隔）" maxlength="200" />
          <label class="enable-label">
            <input v-model="editEnabled" type="checkbox" /> 启用该文档参与检索
          </label>
          <div class="edit-actions">
            <button type="button" class="save-btn" @click="saveEdit(doc)">保存</button>
            <button type="button" class="cancel-btn" @click="editingId = null">取消</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useDocumentStore } from '@/stores/document'
import DocumentUploader from '@/components/DocumentUploader.vue'
import DocumentStatusBadge from '@/components/DocumentStatusBadge.vue'
import { getDocuments, deleteDocument, updateDocument } from '@/api/document'
import { extractError } from '@/utils/error'

const documentStore = useDocumentStore()
const loading = ref(true)
const loadError = ref<string | null>(null)

// 知识库结构化：分类过滤 + 文档元数据编辑
const categoryFilter = ref('')
const editingId = ref<string | null>(null)
const editCategory = ref('')
const editTags = ref('')
const editEnabled = ref(true)

const categories = computed(() =>
  Array.from(
    new Set(
      (documentStore.documents as any[])
        .map((d) => d.category)
        .filter((c) => typeof c === 'string' && c.length > 0)
    )
  ).sort() as string[]
)

const filteredDocs = computed(() => {
  if (!categoryFilter.value) return documentStore.documents
  return (documentStore.documents as any[]).filter(
    (d) => d.category === categoryFilter.value
  )
})

function toggleEdit(doc: any) {
  if (editingId.value === doc.document_id) {
    editingId.value = null
    return
  }
  editingId.value = doc.document_id
  editCategory.value = doc.category || ''
  editTags.value = doc.tags || ''
  editEnabled.value = doc.is_enabled !== 0
}

async function saveEdit(doc: any) {
  try {
    await updateDocument(doc.document_id, {
      category: editCategory.value.trim() || null,
      tags: editTags.value.trim() || null,
      is_enabled: editEnabled.value ? 1 : 0,
    })
    editingId.value = null
    await loadDocuments()
  } catch (e: any) {
    alert(`保存失败: ${extractError(e)}`)
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null

/** 是否存在未进入终态（ready/failed）的文档，决定是否继续轮询 */
function hasActiveDocs(): boolean {
  return documentStore.documents.some((d) =>
    ['pending', 'parsing', 'chunking', 'embedding', 'indexing'].includes(d.status)
  )
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    await loadDocuments()
    if (!hasActiveDocs()) stopPolling()
  }, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(async () => {
  try {
    await loadDocuments()
    // 有中间态文档时自动轮询，直到全部就绪/失败
    if (hasActiveDocs()) startPolling()
  } finally {
    loading.value = false
  }
})

onUnmounted(stopPolling)

async function loadDocuments() {
  loadError.value = null
  try {
    const data = await getDocuments()
    documentStore.setDocuments(data.documents)
  } catch (e: any) {
    loadError.value = `加载文档列表失败: ${e?.message || e}`
  }
}

function onUploaded() {
  loadDocuments()
  // 上传即开始轮询状态（pending → ... → ready/failed）
  startPolling()
}

async function deleteDoc(id: string) {
  if (!confirm('确定要删除这个文档吗？删除后不可恢复。')) return
  try {
    await deleteDocument(id)
    documentStore.removeDocument(id)
  } catch (e: any) {
    alert(`删除失败: ${extractError(e)}`)
  }
}

function formatTime(iso: string): string {
  if (!iso) return ''
  return iso.replace('T', ' ').slice(0, 19)
}
</script>

<style scoped>
.knowledge-view {
  max-width: 900px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 1.25rem;
}
.page-header h1 {
  font-size: 1.4rem;
  color: #333;
}
.subtitle {
  color: #888;
  font-size: 0.9rem;
  margin-top: 0.25rem;
}

.upload-area {
  margin-bottom: 1rem;
  padding: 1rem;
  background: #fff;
  border: 1px dashed #ccc;
  border-radius: 12px;
  text-align: center;
}

.load-error {
  background: #fff3f3;
  color: var(--danger);
  padding: 0.75rem 1rem;
  border-radius: 8px;
  margin-bottom: 1rem;
  font-size: 0.9rem;
}

.empty-state {
  text-align: center;
  padding: 3rem 0;
  color: #999;
}
.empty-icon {
  font-size: 3rem;
  margin-bottom: 0.75rem;
}

.document-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.document-card {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1rem 1.25rem;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  transition: all 0.2s;
}
.document-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
  transform: translateY(-1px);
}

.doc-icon {
  font-size: 1.8rem;
}

.doc-info {
  flex: 1;
  min-width: 0;
}
.doc-title {
  font-weight: 500;
  color: #333;
  margin-bottom: 0.3rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-meta {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.meta-item {
  color: #999;
  font-size: 0.8rem;
}

.doc-actions {
  display: flex;
  gap: 0.5rem;
  flex-shrink: 0;
}
.action-link {
  text-decoration: none;
  color: var(--primary);
  padding: 0.35rem 0.75rem;
  border-radius: 6px;
  font-size: 0.85rem;
  transition: all 0.2s;
}
.action-link:hover {
  background: var(--primary-soft);
}
.delete-btn {
  background: transparent;
  color: var(--danger);
  border: 1px solid #ffcdd2;
  padding: 0.35rem 0.75rem;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.85rem;
  transition: all 0.2s;
}
.delete-btn:hover {
  background: var(--danger-soft);
}

.edit-btn {
  background: transparent;
  color: var(--primary);
  border: 1px solid #bbdefb;
  padding: 0.35rem 0.75rem;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.85rem;
  transition: all 0.2s;
}
.edit-btn:hover {
  background: var(--primary-soft);
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 1rem;
}
.filter-label {
  color: #888;
  font-size: 0.9rem;
}
.filter-select {
  padding: 0.4rem 0.75rem;
  border: 1.5px solid #ddd;
  border-radius: 8px;
  font-size: 0.9rem;
  outline: none;
}

.cat-tag {
  background: #e8f5e9;
  color: #2e7d32;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  font-size: 0.78rem;
}
.tags-text {
  color: var(--primary);
  font-size: 0.8rem;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.disabled-tag {
  background: var(--danger-soft);
  color: #c62828;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  font-size: 0.78rem;
}

.doc-edit {
  flex-basis: 100%;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
  padding: 0.75rem;
  background: var(--bg);
  border-radius: 8px;
  margin-top: 0.5rem;
}
.edit-input {
  flex: 1;
  min-width: 140px;
  padding: 0.45rem 0.7rem;
  border: 1.5px solid #ddd;
  border-radius: 8px;
  font-size: 0.85rem;
  outline: none;
}
.edit-input:focus {
  border-color: var(--primary);
}
.enable-label {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.85rem;
  color: #555;
}
.edit-actions {
  display: flex;
  gap: 0.5rem;
}
.save-btn {
  background: var(--primary);
  color: #fff;
  border: none;
  padding: 0.4rem 1rem;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.85rem;
}
.save-btn:hover {
  background: var(--primary-hover);
}
.cancel-btn {
  background: transparent;
  color: #666;
  border: 1px solid #ddd;
  padding: 0.4rem 1rem;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.85rem;
}
.cancel-btn:hover {
  background: #f0f0f0;
}
</style>

<template>
  <div class="document-detail">
    <div class="detail-header">
      <router-link to="/knowledge" class="back-link">← 返回知识库</router-link>
      <h1>{{ document?.file_name || '文档详情' }}</h1>
    </div>

    <div v-if="document" class="detail-card">
      <div class="detail-row">
        <span class="label">文件名</span>
        <span class="value">{{ document.file_name }}</span>
      </div>
      <div class="detail-row">
        <span class="label">标题</span>
        <span class="value">{{ document.document_title }}</span>
      </div>
      <div class="detail-row">
        <span class="label">状态</span>
        <span class="value"><DocumentStatusBadge :status="document.status" /></span>
      </div>
      <div class="detail-row">
        <span class="label">大小</span>
        <span class="value">{{ formatSize(document.file_size) }}</span>
      </div>
      <div class="detail-row">
        <span class="label">片段数</span>
        <span class="value">{{ document.chunk_count }}</span>
      </div>
      <div class="detail-row">
        <span class="label">上传时间</span>
        <span class="value">{{ document.created_at }}</span>
      </div>
      <div v-if="document.error_msg" class="detail-row error">
        <span class="label">错误</span>
        <span class="value">{{ document.error_msg }}</span>
      </div>
    </div>
    <div v-else-if="loading" class="loading">加载中...</div>
    <div v-else class="loading error">文档不存在或加载失败</div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { getDocument } from '@/api/document'
import DocumentStatusBadge from '@/components/DocumentStatusBadge.vue'

const route = useRoute()
const document = ref<any>(null)
const loading = ref(true)

onMounted(async () => {
  const id = route.params.id as string
  try {
    document.value = await getDocument(id)
  } catch (e) {
    document.value = null
  } finally {
    loading.value = false
  }
})

function formatSize(bytes: number): string {
  if (!bytes && bytes !== 0) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<style scoped>
.document-detail {
  max-width: 720px;
  margin: 0 auto;
}

.detail-header {
  margin-bottom: 1.25rem;
}
.detail-header h1 {
  font-size: 1.3rem;
  color: #333;
  margin-top: 0.5rem;
  word-break: break-all;
}
.back-link {
  text-decoration: none;
  color: #1976d2;
  font-size: 0.9rem;
}
.back-link:hover {
  text-decoration: underline;
}

.detail-card {
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 12px;
  overflow: hidden;
}
.detail-row {
  display: flex;
  padding: 0.9rem 1.25rem;
  border-bottom: 1px solid #f0f0f0;
}
.detail-row:last-child {
  border-bottom: none;
}
.detail-row .label {
  width: 110px;
  flex-shrink: 0;
  color: #888;
  font-weight: 500;
}
.detail-row .value {
  flex: 1;
  word-break: break-all;
}
.detail-row.error .value {
  color: #d32f2f;
}

.loading {
  text-align: center;
  color: #999;
  padding: 3rem 0;
}

.loading.error {
  color: #d32f2f;
}
</style>

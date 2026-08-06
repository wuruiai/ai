<template>
  <div class="document-uploader">
    <input
      type="file"
      ref="fileInput"
      accept=".pdf,.docx,.txt,.md"
      @change="onFileChange"
      style="display: none"
    />
    <button @click="triggerUpload" :disabled="isUploading">
      {{ isUploading ? '上传中...' : '上传文档' }}
    </button>
    <span v-if="selectedFile" class="filename">{{ selectedFile.name }}</span>
    <span v-if="error" class="upload-error">⚠️ {{ error }}</span>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { uploadDocument } from '@/api/document'
import { extractError } from '@/utils/error'

const emit = defineEmits<{
  uploaded: []
}>()

const fileInput = ref<HTMLInputElement | null>(null)
const selectedFile = ref<File | null>(null)
const isUploading = ref(false)
const error = ref<string | null>(null)

function triggerUpload() {
  error.value = null
  fileInput.value?.click()
}

function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  if (target.files?.length) {
    selectedFile.value = target.files[0]
    doUpload()
  }
}

async function doUpload() {
  if (!selectedFile.value) return

  isUploading.value = true
  error.value = null
  try {
    await uploadDocument(selectedFile.value)
    selectedFile.value = null
    emit('uploaded')
  } catch (e: any) {
    console.error('Upload failed:', e)
    // 展示后端 detail 或通用错误
    error.value = extractError(e, '上传失败')
    selectedFile.value = null
  } finally {
    isUploading.value = false
    // 成功与失败都重置 input，否则同一文件无法再次触发 change（要刷新页面才能重传）
    if (fileInput.value) fileInput.value.value = ''
  }
}
</script>

<style scoped>
.document-uploader {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  flex-wrap: wrap;
}

button {
  padding: 9px 22px;
  background: var(--primary);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  transition: all 0.15s ease;
  font-family: var(--font);
  box-shadow: var(--shadow-sm);
}

button:hover:not(:disabled) {
  background: var(--primary-hover);
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}

button:disabled {
  background: var(--text-3);
  cursor: not-allowed;
}

.filename {
  color: #666;
  font-size: 0.9rem;
}

.upload-error {
  color: #d32f2f;
  font-size: 0.85rem;
  flex-basis: 100%;
  text-align: center;
}
</style>

<template>
  <div class="evidence-preview">
    <div class="preview-header">来源预览</div>
    <div class="preview-content">{{ content }}</div>
    <div class="preview-meta">
      <span>文档: {{ sourceName }}</span>
      <span>页码: {{ page }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  content: string
  sourceName: string
  page: number
}>()
</script>

<style scoped>
.evidence-preview {
  padding: 1rem;
  background: #f5f5f5;
  border-radius: 8px;
  margin-top: 0.5rem;
}

.preview-header {
  font-weight: bold;
  margin-bottom: 0.5rem;
}

.preview-content {
  margin-bottom: 0.5rem;
  line-height: 1.6;
}

.preview-meta {
  display: flex;
  gap: 1rem;
  font-size: 0.9em;
  color: #666;
}
</style>

<template>
  <div class="citation-panel">
    <div class="citation-header">📎 来源引用</div>
    <div class="citation-list">
      <div
        v-for="(citation, index) in citations"
        :key="index"
        class="citation-item"
      >
        <span class="citation-index">[{{ index + 1 }}]</span>
        <div class="citation-body">
          <div class="citation-source">{{ citation.source_name || '未知来源' }}</div>
          <div v-if="citation.content" class="citation-snippet">{{ citation.content }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  citations: Array<{
    source_name?: string
    page?: number | null
    content?: string
  }>
}>()
</script>

<style scoped>
.citation-panel {
  margin-top: 0.75rem;
  padding: 12px 14px;
  background: var(--primary-soft);
  border: 1px solid #bfdbfe;
  border-radius: var(--radius-sm);
  font-size: 13px;
}

.citation-header {
  font-weight: 600;
  color: var(--primary);
  margin-bottom: 0.5rem;
}

.citation-list {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.citation-item {
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
}

.citation-index {
  font-weight: 700;
  color: #1976d2;
  flex-shrink: 0;
  margin-top: 0.1em;
}

.citation-body {
  flex: 1;
  min-width: 0;
}

.citation-source {
  font-weight: 500;
  color: #455a64;
}

.citation-snippet {
  color: #78909c;
  margin-top: 0.15rem;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
</style>

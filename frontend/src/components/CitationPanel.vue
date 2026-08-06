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
          <div class="citation-source">
            {{ citation.source_name || '未知来源' }}
            <span v-if="citation.verified === true" class="badge verified">已核实</span>
            <span v-else-if="citation.verified === false" class="badge pending">待核实</span>
          </div>
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
    /** G3.2：后端引用核实结果；undefined=未判定（流式展示阶段） */
    verified?: boolean
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

.badge {
  display: inline-block;
  margin-left: 0.5rem;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 11px;
  line-height: 1.5;
  vertical-align: 0.05em;
}

.badge.verified {
  color: #1b873f;
  background: #e7f7ec;
  border: 1px solid #b7e0c4;
}

.badge.pending {
  color: #b45309;
  background: #fef3c7;
  border: 1px solid #f5d78e;
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

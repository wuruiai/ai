<template>
  <span :class="['status-badge', status]">
    {{ statusText }}
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  status: string
}>()

const statusText = computed(() => {
  const map: Record<string, string> = {
    pending: '待处理',
    parsing: '解析中',
    chunking: '切分中',
    embedding: '向量化中',
    indexing: '索引中',
    ready: '就绪',
    failed: '失败',
  }
  return map[props.status] || props.status
})
</script>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.6;
}

.status-badge.pending {
  background: var(--warning-soft);
  color: var(--warning);
}

.status-badge.parsing,
.status-badge.chunking,
.status-badge.embedding,
.status-badge.indexing {
  background: var(--primary-soft);
  color: var(--primary);
}

.status-badge.ready {
  background: var(--success-soft);
  color: var(--success);
}

.status-badge.failed {
  background: var(--danger-soft);
  color: var(--danger);
}
</style>

<template>
  <div class="high-risk-warning">
    <div class="warning-icon">⚠️</div>
    <div class="warning-content">
      <div class="warning-title">高风险提示</div>
      <div class="warning-message">{{ message }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  message: string
}>()
</script>

<style scoped>
.high-risk-warning {
  display: flex;
  gap: 0.75rem;
  padding: 12px 14px;
  background: var(--warning-soft);
  border: 1px solid #fcd34d;
  border-left: 4px solid var(--warning);
  border-radius: var(--radius-sm);
  margin: 12px 0;
}

.warning-icon {
  font-size: 1.25rem;
}

.warning-title {
  font-weight: 600;
  color: var(--warning);
  margin-bottom: 0.2rem;
  font-size: 13px;
}

.warning-message {
  color: #92400e;
  font-size: 13px;
  line-height: 1.5;
}
</style>

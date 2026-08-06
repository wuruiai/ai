<template>
  <div v-if="hasError" class="error-boundary">
    <div class="eb-card" role="alert">
      <div class="eb-icon">⚠️</div>
      <div class="eb-title">页面渲染异常</div>
      <div class="eb-detail">{{ message || '未知错误' }}</div>
      <button type="button" class="eb-btn" @click="reload">重新加载</button>
    </div>
  </div>
  <slot v-else />
</template>

<script setup lang="ts">
import { onErrorCaptured, ref } from 'vue'

/**
 * 全局错误边界（G6.3）：子组件渲染抛错时显示降级卡片，避免整页白屏。
 * `errorCaptured` 返回 false 阻止错误继续向根冒泡，就地接管。
 * 只捕获渲染/生命周期错误；事件处理器中的异常走各自的业务错误处理。
 */
const hasError = ref(false)
const message = ref('')

onErrorCaptured((err) => {
  hasError.value = true
  message.value = err instanceof Error ? err.message : String(err)
  return false
})

function reload() {
  window.location.reload()
}
</script>

<style scoped>
.error-boundary {
  min-height: 60vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.eb-card {
  max-width: 420px;
  width: 100%;
  padding: 32px 36px;
  text-align: center;
  background: #fff;
  border: 1px solid #fecaca;
  border-radius: var(--radius-lg, 12px);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
}
.eb-icon {
  font-size: 40px;
}
.eb-title {
  margin-top: 12px;
  font-size: 16px;
  font-weight: 700;
  color: var(--danger, #d32f2f);
}
.eb-detail {
  margin-top: 8px;
  font-size: 13px;
  color: #64748b;
  word-break: break-all;
}
.eb-btn {
  margin-top: 20px;
  padding: 8px 24px;
  border: none;
  border-radius: var(--radius-sm, 8px);
  background: var(--primary, #1976d2);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.eb-btn:hover {
  opacity: 0.9;
}
</style>

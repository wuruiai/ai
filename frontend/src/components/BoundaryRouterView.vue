<template>
  <!-- 路由出口统一包装（G6.3）：
       1) ErrorBoundary：渲染异常降级不白屏
       2) Suspense：懒加载路由 chunk 期间显示骨架，不闪白屏 -->
  <ErrorBoundary>
    <router-view v-slot="{ Component }">
      <Suspense>
        <template #default>
          <component :is="Component" />
        </template>
        <template #fallback>
          <div class="route-skeleton" aria-busy="true">
            <div class="skeleton-line w-60" />
            <div class="skeleton-line w-90" />
            <div class="skeleton-line w-75" />
            <div class="skeleton-block" />
          </div>
        </template>
      </Suspense>
    </router-view>
  </ErrorBoundary>
</template>

<script setup lang="ts">
import ErrorBoundary from '@/components/ErrorBoundary.vue'
</script>

<style scoped>
.route-skeleton {
  padding: 8px 4px;
}
.skeleton-line,
.skeleton-block {
  border-radius: 8px;
  background: linear-gradient(
    90deg,
    rgba(148, 163, 184, 0.15) 25%,
    rgba(148, 163, 184, 0.32) 50%,
    rgba(148, 163, 184, 0.15) 75%
  );
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.4s ease-in-out infinite;
}
.skeleton-line {
  height: 14px;
  margin-bottom: 14px;
}
.skeleton-block {
  height: 120px;
  margin-top: 20px;
}
.w-60 {
  width: 60%;
}
.w-90 {
  width: 90%;
}
.w-75 {
  width: 75%;
}

@keyframes skeleton-shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}
</style>

<template>
  <div id="app">
    <!-- 登录页：无侧边栏 -->
    <template v-if="route.path === '/login'">
      <BoundaryRouterView />
    </template>

    <!-- 主布局：深色侧边栏 + 内容区 -->
    <div v-else class="app-shell">
      <aside class="sidebar">
        <div class="sidebar-brand">
          <div class="brand-icon">💧</div>
          <div class="brand-text">
            <div class="brand-name">水利 RAG</div>
            <div class="brand-sub">知识问答平台</div>
          </div>
        </div>

        <nav class="sidebar-nav">
          <router-link to="/" class="nav-item">
            <span class="nav-icon">💬</span>
            <span class="nav-label">对话</span>
          </router-link>
          <router-link to="/knowledge" class="nav-item">
            <span class="nav-icon">📚</span>
            <span class="nav-label">知识库</span>
          </router-link>
          <router-link v-if="authStore.isAdmin" to="/admin" class="nav-item">
            <span class="nav-icon">📊</span>
            <span class="nav-label">管理看板</span>
          </router-link>
          <router-link to="/settings" class="nav-item">
            <span class="nav-icon">⚙️</span>
            <span class="nav-label">设置</span>
          </router-link>
        </nav>

        <div class="sidebar-footer">
          <div class="user-block">
            <div class="user-avatar">
              {{ avatarChar }}
            </div>
            <div class="user-meta">
              <div class="user-name">{{ authStore.user?.display_name || authStore.user?.username || '未登录' }}</div>
              <div class="user-role">{{ authStore.isAdmin ? '管理员' : '普通用户' }}</div>
            </div>
            <button class="logout-btn" title="退出登录" @click="logout">⎋</button>
          </div>
        </div>
      </aside>

      <main class="main-area">
        <BoundaryRouterView />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import BoundaryRouterView from '@/components/BoundaryRouterView.vue'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const avatarChar = computed(() =>
  (authStore.user?.display_name || authStore.user?.username || '?').charAt(0).toUpperCase()
)

function logout() {
  authStore.logout()
  router.push('/login')
}
</script>

<style>
.app-shell {
  display: flex;
  min-height: 100vh;
}

/* ---------- 侧边栏 ---------- */
.sidebar {
  width: 232px;
  flex-shrink: 0;
  background: var(--sidebar-bg);
  color: var(--sidebar-text);
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 20px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.brand-icon {
  font-size: 26px;
}
.brand-text {
  line-height: 1.2;
}
.brand-name {
  font-size: 16px;
  font-weight: 700;
  color: #fff;
}
.brand-sub {
  font-size: 11px;
  color: var(--sidebar-text);
  margin-top: 2px;
}

.sidebar-nav {
  flex: 1;
  padding: 14px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  color: var(--sidebar-text);
  font-size: 14px;
  font-weight: 500;
  transition: all 0.15s ease;
}
.nav-item:hover {
  background: var(--sidebar-hover);
  color: var(--sidebar-text-active);
}
.nav-item.router-link-active {
  background: var(--sidebar-active);
  color: var(--sidebar-text-active);
  box-shadow: var(--shadow-sm);
}
.nav-icon {
  font-size: 16px;
  width: 20px;
  text-align: center;
}

.sidebar-footer {
  padding: 12px 10px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}
.user-block {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.03);
}
.user-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: linear-gradient(135deg, #3b82f6, #0ea5e9);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 14px;
  flex-shrink: 0;
}
.user-meta {
  flex: 1;
  min-width: 0;
  line-height: 1.2;
}
.user-name {
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.user-role {
  font-size: 11px;
  color: var(--sidebar-text);
  margin-top: 2px;
}
.logout-btn {
  border: none;
  background: transparent;
  color: var(--sidebar-text);
  font-size: 15px;
  cursor: pointer;
  padding: 4px 6px;
  border-radius: 6px;
  transition: all 0.15s ease;
}
.logout-btn:hover {
  background: rgba(220, 38, 38, 0.2);
  color: #fca5a5;
}

/* ---------- 内容区 ---------- */
.main-area {
  flex: 1;
  min-width: 0;
  padding: 24px;
  overflow-y: auto;
}
</style>

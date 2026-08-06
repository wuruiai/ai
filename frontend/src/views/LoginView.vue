<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand">💧 水利 RAG + Agent</div>
      <div class="tabs">
        <button type="button" :class="['tab', { active: mode === 'login' }]" @click="mode = 'login'">登录</button>
        <button type="button" :class="['tab', { active: mode === 'register' }]" @click="mode = 'register'">注册</button>
      </div>
      <form @submit.prevent="submit">
        <label class="field-label" for="login-username">用户名</label>
        <input id="login-username" v-model="username" class="field" required minlength="3" maxlength="32"
               placeholder="字母 / 数字 / 下划线" autocomplete="username" />
        <label class="field-label" for="login-password">密码</label>
        <input id="login-password" v-model="password" type="password" class="field" required minlength="6"
               :placeholder="mode === 'register' ? '至少 6 位' : '请输入密码'" autocomplete="current-password" />
        <label v-if="mode === 'register'" class="field-label" for="login-display-name">显示名称</label>
        <input v-if="mode === 'register'" id="login-display-name" v-model="displayName" class="field" maxlength="64"
               placeholder="可选" autocomplete="nickname" />
        <div v-if="error" class="login-error">⚠️ {{ error }}</div>
        <button type="submit" class="submit-btn" :disabled="loading">
          {{ loading ? '处理中...' : mode === 'login' ? '登 录' : '注 册' }}
        </button>
      </form>
      <div class="login-hint">首个注册用户自动成为管理员</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { extractError } from '@/utils/error'

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const displayName = ref('')
const error = ref('')
const loading = ref(false)

const authStore = useAuthStore()
const router = useRouter()

async function submit() {
  error.value = ''
  loading.value = true
  try {
    if (mode.value === 'login') {
      await authStore.login(username.value.trim(), password.value)
    } else {
      await authStore.register(
        username.value.trim(),
        password.value,
        displayName.value.trim() || undefined
      )
    }
    router.push('/')
  } catch (e: any) {
    error.value = extractError(e)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #e3f2fd, #f5f7fa);
  padding: 1rem;
}

.login-card {
  width: 100%;
  max-width: 380px;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.08);
  padding: 2rem 2.25rem;
}

.login-brand {
  font-size: 1.2rem;
  font-weight: 700;
  color: var(--primary);
  text-align: center;
  margin-bottom: 1.25rem;
}

.tabs {
  display: flex;
  background: #f0f4ff;
  border-radius: 10px;
  padding: 0.25rem;
  margin-bottom: 1.5rem;
}
.tab {
  flex: 1;
  border: none;
  background: transparent;
  padding: 0.5rem;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.95rem;
  color: #666;
  transition: all 0.2s;
}
.tab.active {
  background: #fff;
  color: var(--primary);
  font-weight: 600;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
}

.field-label {
  display: block;
  font-size: 0.85rem;
  color: #666;
  margin: 0.9rem 0 0.35rem;
}
.field {
  width: 100%;
  padding: 0.7rem 0.85rem;
  border: 1.5px solid #ddd;
  border-radius: 10px;
  font-size: 0.95rem;
  outline: none;
  transition: border-color 0.2s;
}
.field:focus {
  border-color: var(--primary);
}

.login-error {
  background: #fff3f3;
  color: #d32f2f;
  padding: 0.6rem 0.8rem;
  border-radius: 8px;
  font-size: 0.85rem;
  margin-top: 1rem;
}

.submit-btn {
  width: 100%;
  margin-top: 1.5rem;
  padding: 0.75rem;
  background: linear-gradient(135deg, var(--primary), var(--primary-hover));
  color: #fff;
  border: none;
  border-radius: 10px;
  font-size: 1rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}
.submit-btn:hover:not(:disabled) {
  box-shadow: 0 4px 14px rgba(25, 118, 210, 0.35);
  transform: translateY(-1px);
}
.submit-btn:disabled {
  background: #bdbdbd;
  cursor: not-allowed;
}

.login-hint {
  text-align: center;
  color: #999;
  font-size: 0.8rem;
  margin-top: 1rem;
}
</style>

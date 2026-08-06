<template>
  <div class="settings-view">
    <h1>设置</h1>

    <!-- 账号安全：修改密码 -->
    <div class="settings-content">
      <h2>账号安全</h2>
      <form @submit.prevent="changePassword" class="pw-form">
        <label class="field-label">旧密码</label>
        <input v-model="oldPw" type="password" class="field" required autocomplete="current-password" />
        <label class="field-label">新密码（至少 6 位）</label>
        <input v-model="newPw" type="password" class="field" required minlength="6" autocomplete="new-password" />
        <label class="field-label">确认新密码</label>
        <input v-model="confirmPw" type="password" class="field" required minlength="6" autocomplete="new-password" />
        <div v-if="pwMsg" :class="['msg', pwOk ? 'ok' : 'err']">{{ pwMsg }}</div>
        <button type="submit" class="submit-btn" :disabled="pwSaving">{{ pwSaving ? '保存中...' : '修改密码' }}</button>
      </form>
    </div>

    <!-- 模型配置：仅管理员可见 -->
    <div class="settings-content">
      <h2>模型配置</h2>
      <template v-if="authStore.isAdmin">
        <table class="config-table">
          <tbody>
            <tr><td>环境</td><td>{{ diagnostics.app_env || '-' }}</td></tr>
            <tr><td>LLM 模型</td><td>{{ diagnostics.llm_model || '-' }}</td></tr>
            <tr><td>Embedding 模型</td><td>{{ diagnostics.embedding_model || '-' }}</td></tr>
            <tr><td>Rerank 模型</td><td>{{ diagnostics.rerank_model || '-' }}</td></tr>
            <tr><td>API Key</td>
              <td>
                <span :class="['key-status', diagnostics.api_key_configured ? 'ok' : 'missing']">
                  {{ diagnostics.api_key_configured ? '已配置' : '未配置' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
        <button @click="loadDiagnostics" :disabled="loading" class="refresh-btn">
          {{ loading ? '加载中...' : '刷新' }}
        </button>
      </template>
      <p v-else class="hint">模型配置仅管理员可见。</p>
      <div v-if="error" class="error">{{ error }}</div>
    </div>

    <div class="settings-content">
      <h2>运维</h2>
      <p class="hint">可用脚本：<code>python -m scripts.verify_env --all</code> 完整自检；<code>python -m scripts.backup_data</code> 备份数据。</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { extractError } from '@/utils/error'

const authStore = useAuthStore()

interface Diagnostics {
  app_env?: string
  llm_model?: string
  embedding_model?: string
  rerank_model?: string
  api_key_configured?: boolean
}

const diagnostics = ref<Diagnostics>({})
const loading = ref(false)
const error = ref<string | null>(null)

const oldPw = ref('')
const newPw = ref('')
const confirmPw = ref('')
const pwMsg = ref('')
const pwOk = ref(false)
const pwSaving = ref(false)

async function changePassword() {
  pwMsg.value = ''
  if (newPw.value !== confirmPw.value) {
    pwMsg.value = '两次输入的新密码不一致'
    pwOk.value = false
    return
  }
  pwSaving.value = true
  try {
    await client.post('/auth/change-password', {
      old_password: oldPw.value,
      new_password: newPw.value,
    })
    pwMsg.value = '密码已修改'
    pwOk.value = true
    oldPw.value = newPw.value = confirmPw.value = ''
  } catch (e: any) {
    pwMsg.value = extractError(e, '修改失败')
    pwOk.value = false
  } finally {
    pwSaving.value = false
  }
}

async function loadDiagnostics() {
  loading.value = true
  error.value = null
  try {
    const resp = await client.get('/diagnostics/')
    diagnostics.value = resp.data as Diagnostics
  } catch (e: any) {
    error.value = `加载诊断信息失败: ${e?.message || e}`
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (authStore.isAdmin) loadDiagnostics()
})
</script>

<style scoped>
.settings-view {
  padding: 1rem;
  max-width: 720px;
  margin: 0 auto;
}

.settings-content {
  margin-bottom: 1.5rem;
  padding: 1.25rem;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
}

.settings-content h2 {
  font-size: 1.05rem;
  color: #333;
  margin-bottom: 0.75rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid #f0f0f0;
}

.config-table {
  width: 100%;
  border-collapse: collapse;
}
.config-table td {
  padding: 0.5rem 0;
  border-bottom: 1px solid #e0e0e0;
  font-size: 0.95rem;
}
.config-table td:first-child {
  width: 160px;
  color: #555;
  font-weight: 600;
}

.pw-form {
  max-width: 420px;
}
.field-label {
  display: block;
  font-size: 0.85rem;
  color: #666;
  margin: 0.7rem 0 0.3rem;
}
.field {
  width: 100%;
  padding: 0.6rem 0.85rem;
  border: 1.5px solid #ddd;
  border-radius: 10px;
  font-size: 0.95rem;
  outline: none;
}
.field:focus {
  border-color: var(--primary);
}
.submit-btn {
  margin-top: 1rem;
  padding: 0.6rem 1.5rem;
  background: var(--primary);
  color: #fff;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.95rem;
}
.submit-btn:hover:not(:disabled) {
  background: var(--primary-hover);
}
.submit-btn:disabled {
  background: #bdbdbd;
  cursor: not-allowed;
}
.msg {
  margin-top: 0.7rem;
  font-size: 0.9rem;
}
.msg.ok {
  color: #2e7d32;
}
.msg.err {
  color: var(--danger);
}

.key-status {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.85em;
}
.key-status.ok {
  background: #e8f5e9;
  color: #2e7d32;
}
.key-status.missing {
  background: #ffebee;
  color: var(--danger);
}

.hint {
  color: #666;
  font-size: 0.9rem;
}
.hint code {
  background: #eee;
  padding: 1px 4px;
  border-radius: 3px;
}

.refresh-btn {
  margin-top: 0.5rem;
  padding: 0.4rem 1rem;
  background: var(--primary);
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}
.refresh-btn:disabled {
  background: #ccc;
}

.error {
  color: var(--danger);
  margin-top: 0.5rem;
  font-size: 0.9rem;
}
</style>

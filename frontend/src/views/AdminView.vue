<template>
  <div class="admin-view">
    <div class="page-header">
      <h1>管理看板</h1>
      <p class="subtitle">系统用量、用户、文档与反馈统计（仅管理员可见）</p>
    </div>

    <div v-if="error" class="load-error">⚠️ {{ error }}</div>

    <div v-if="stats" class="stat-grid">
      <div class="stat-card">
        <div class="stat-value">{{ stats.user_count }}</div>
        <div class="stat-label">用户总数</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ stats.document_count }}</div>
        <div class="stat-label">文档总数</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ stats.chunk_count }}</div>
        <div class="stat-label">知识片段</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ stats.message_count }}</div>
        <div class="stat-label">消息总数</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ stats.helpful_count }}<span class="stat-sub"> / {{ stats.feedback_count }}</span></div>
        <div class="stat-label">反馈好评 / 总数</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ stats.audit_count }}</div>
        <div class="stat-label">审计记录</div>
      </div>
    </div>

    <div v-if="daily" class="section">
      <h2 class="section-title">近 14 天消息趋势</h2>
      <svg viewBox="0 0 720 190" class="trend-chart" role="img" aria-label="近14天每日消息量柱状图">
        <line x1="0" y1="150" x2="720" y2="150" class="chart-baseline" />
        <rect
          v-for="b in chartBars()"
          :key="b.day"
          :x="b.x"
          :y="b.y"
          :width="b.w"
          :height="b.h"
          rx="4"
          class="chart-bar"
        >
          <title>{{ b.day }}：{{ b.value }} 条</title>
        </rect>
        <template v-for="(b, i) in chartBars()" :key="'lb' + i">
          <text
            v-if="i % 2 === 0"
            :x="b.x + b.w / 2"
            y="172"
            class="chart-label"
            text-anchor="middle"
          >{{ b.day.slice(5) }}</text>
        </template>
      </svg>
    </div>

    <div class="section">
      <h2 class="section-title">数据导出</h2>
      <div class="export-actions">
        <button class="btn" @click="exportData('threads')">📄 导出对话记录 (CSV)</button>
        <button class="btn" @click="exportData('feedback')">📊 导出反馈 (CSV)</button>
      </div>
    </div>

    <div v-if="users" class="section">
      <h2 class="section-title">用户管理</h2>
      <table class="audit-table">
        <thead>
          <tr>
            <th>用户名</th>
            <th>显示名</th>
            <th>角色</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.user_id">
            <td>{{ u.username }}</td>
            <td>{{ u.display_name || '-' }}</td>
            <td>
              <select :value="u.role" class="role-select" @change="changeRole(u, ($event.target as HTMLSelectElement).value)">
                <option value="user">用户</option>
                <option value="admin">管理员</option>
              </select>
            </td>
            <td>
              <span :class="['user-status', u.is_active ? 'on' : 'off']">
                {{ u.is_active ? '启用' : '禁用' }}
              </span>
            </td>
            <td>
              <button class="mini-btn" @click="toggleActive(u)">
                {{ u.is_active ? '禁用' : '启用' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="stats" class="section">
      <h2 class="section-title">最近操作审计</h2>
      <table class="audit-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>用户</th>
            <th>动作</th>
            <th>对象</th>
            <th>详情</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="log in recentAudit" :key="log.log_id">
            <td>{{ formatTime(log.created_at) }}</td>
            <td>{{ log.username || log.user_id }}</td>
            <td><span class="action-tag">{{ log.action }}</span></td>
            <td>{{ log.target_type }} {{ shortId(log.target_id) }}</td>
            <td class="detail-cell">{{ log.detail }}</td>
          </tr>
          <tr v-if="!recentAudit.length">
            <td colspan="5" class="empty-cell">暂无审计记录</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import client from '@/api/client'
import { extractError } from '@/utils/error'

interface Stats {
  user_count: number
  document_count: number
  chunk_count: number
  message_count: number
  feedback_count: number
  helpful_count: number
  audit_count: number
}

interface AuditLog {
  log_id: string
  username?: string
  user_id?: string
  action: string
  target_type?: string
  target_id?: string
  detail?: string
  created_at: string
}

interface AdminUser {
  user_id: string
  username: string
  display_name?: string
  role: string
  is_active: number
}

interface DailyStats {
  days: string[]
  messages: number[]
  uploads: number[]
}

const stats = ref<Stats | null>(null)
const recentAudit = ref<AuditLog[]>([])
const users = ref<AdminUser[]>([])
const daily = ref<DailyStats | null>(null)
const error = ref('')

async function loadDaily() {
  try {
    const res = await client.get('/admin/stats/daily')
    daily.value = res.data
  } catch {
    // 趋势图加载失败不阻断其它区块
  }
}

/** 计算柱状图几何：固定 720 宽、基线 y=150 */
function chartBars() {
  if (!daily.value || !daily.value.days.length) return []
  const W = 720
  const base = 150
  const pad = 8
  const arr = daily.value.messages
  const max = Math.max(1, ...arr)
  const n = arr.length
  const slot = W / n
  const barW = Math.min(24, slot * 0.6)
  return daily.value.days.map((day, i) => {
    const h = (arr[i] / max) * (base - pad)
    return { day, value: arr[i], x: i * slot + (slot - barW) / 2, y: base - h, w: barW, h }
  })
}

/** 导出 CSV：用 axios 拿 blob（带 token），再触发浏览器下载 */
async function exportData(kind: 'threads' | 'feedback') {
  try {
    const res = await client.get(`/admin/export/${kind}`, { responseType: 'blob' })
    const url = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = url
    a.download = `${kind}_export_${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e: any) {
    alert(extractError(e, '导出失败'))
  }
}

async function loadUsers() {
  try {
    const res = await client.get('/admin/users')
    users.value = res.data.users || []
  } catch (e: any) {
    error.value = extractError(e, '加载用户失败')
  }
}

async function changeRole(u: AdminUser, role: string) {
  try {
    await client.patch(`/admin/users/${u.user_id}`, { role })
    u.role = role
  } catch (e: any) {
    alert(extractError(e, '修改角色失败'))
  }
}

async function toggleActive(u: AdminUser) {
  try {
    await client.patch(`/admin/users/${u.user_id}`, { is_active: u.is_active ? 0 : 1 })
    u.is_active = u.is_active ? 0 : 1
  } catch (e: any) {
    alert(extractError(e, '操作失败'))
  }
}

onMounted(async () => {
  try {
    const [statsRes, auditRes] = await Promise.all([
      client.get('/admin/stats'),
      client.get('/admin/audit'),
    ])
    stats.value = statsRes.data
    recentAudit.value = auditRes.data.logs || []
    await loadUsers()
    await loadDaily()
  } catch (e: any) {
    error.value = extractError(e, '加载失败')
  }
})

function formatTime(s: string): string {
  if (!s) return ''
  return s.replace('T', ' ').slice(0, 19)
}
function shortId(id?: string): string {
  return id ? id.slice(0, 12) + '…' : ''
}
</script>

<style scoped>
.admin-view {
  max-width: 1000px;
  margin: 0 auto;
}
.page-header {
  margin-bottom: 1.25rem;
}
.page-header h1 {
  font-size: 1.4rem;
  color: #333;
}
.subtitle {
  color: #888;
  font-size: 0.9rem;
  margin-top: 0.25rem;
}
.load-error {
  background: #fff3f3;
  color: var(--danger);
  padding: 0.75rem 1rem;
  border-radius: 8px;
  margin-bottom: 1rem;
  font-size: 0.9rem;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 0.75rem;
  margin-bottom: 1.5rem;
}
.stat-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1rem;
  text-align: center;
}
.stat-value {
  font-size: 1.6rem;
  font-weight: 700;
  color: var(--primary);
}
.stat-sub {
  font-size: 0.85rem;
  color: #999;
  font-weight: 400;
}
.stat-label {
  color: #888;
  font-size: 0.85rem;
  margin-top: 0.25rem;
}
.section {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1rem 1.25rem;
}
.section-title {
  font-size: 1.05rem;
  color: #333;
  margin-bottom: 0.75rem;
}
.audit-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}
.audit-table th,
.audit-table td {
  text-align: left;
  padding: 0.55rem 0.6rem;
  border-bottom: 1px solid #f0f0f0;
}
.audit-table th {
  color: #999;
  font-weight: 500;
}
.action-tag {
  background: var(--primary-soft);
  color: var(--primary);
  padding: 0.1rem 0.45rem;
  border-radius: 6px;
  font-size: 0.8rem;
}
.detail-cell {
  color: #666;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.empty-cell {
  text-align: center;
  color: #999;
  padding: 1rem;
}

.role-select {
  padding: 0.3rem 0.5rem;
  border: 1.5px solid #ddd;
  border-radius: 6px;
  font-size: 0.85rem;
}
.user-status {
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  font-size: 0.8rem;
}
.user-status.on {
  background: #e8f5e9;
  color: #2e7d32;
}
.user-status.off {
  background: var(--danger-soft);
  color: #c62828;
}
.mini-btn {
  border: 1px solid #ddd;
  background: #fff;
  color: #666;
  padding: 0.25rem 0.7rem;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.8rem;
}
.mini-btn:hover {
  background: #f0f0f0;
}

/* 趋势图表（dataviz：单系列蓝、细条圆角锚定基线、哑轴/哑标签、原生悬浮） */
.trend-chart {
  width: 100%;
  height: auto;
  display: block;
}
.chart-bar {
  fill: #2a78d6;
  transition: fill 0.15s ease;
}
.chart-bar:hover {
  fill: #256abf;
}
.chart-baseline {
  stroke: #c3c2b7;
  stroke-width: 1;
}
.chart-label {
  fill: #898781;
  font-size: 10px;
  font-family: var(--font);
}

.export-actions {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}
</style>

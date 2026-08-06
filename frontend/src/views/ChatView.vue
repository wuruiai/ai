<template>
  <div class="chat-layout">
    <aside class="thread-sidebar">
      <button class="new-chat-btn" @click="newConversation">＋ 新对话</button>
      <div v-if="!chatStore.threads.length" class="no-threads">暂无历史会话</div>
      <ul class="thread-list">
        <li
          v-for="t in chatStore.threads"
          :key="t.thread_id"
          :class="['thread-item', { active: t.thread_id === chatStore.threadId }]"
          @click="switchThread(t.thread_id)"
        >
          <span class="thread-title" :title="t.title">{{ t.title }}</span>
          <button class="thread-delete" title="删除会话" @click.stop="removeThread(t.thread_id)">✕</button>
        </li>
      </ul>
    </aside>

    <div class="chat-view">
    <div class="messages" ref="messagesEl">
      <!-- 空状态 -->
      <div v-if="!chatStore.messages.length" class="empty-state">
        <div class="empty-icon">💧</div>
        <h2>水利知识问答</h2>
        <p>上传文档到知识库后，向我提问水利规范、规程、报告相关内容</p>
      </div>

      <div
        v-for="message in chatStore.messages"
        :key="message.id"
        :class="['message', message.role]"
      >
        <div class="message-avatar">{{ message.role === 'user' ? '👤' : '🤖' }}</div>
        <div class="message-body">
          <div class="message-content" v-html="renderContent(message)"></div>
          <div v-if="message.warning" class="warning-area">
            <HighRiskWarning :message="message.warning" />
          </div>
          <div v-if="message.citations?.length" class="citations">
            <CitationPanel :citations="message.citations" />
          </div>
          <div v-if="message.role === 'assistant'" class="message-footer">
            <button class="footer-btn" title="复制回答" @click="copyMessage(message)">📋 复制</button>
            <button v-if="isLastMessage(message) && chatStore.lastQuery" class="footer-btn" title="重新生成" @click="resend()">🔄 重试</button>
            <FeedbackButton v-if="message.messageId" :message-id="message.messageId" />
          </div>
        </div>
      </div>

      <!-- 打字指示 -->
      <div v-if="chatStore.isLoading" class="typing">
        <span></span><span></span><span></span>
      </div>
    </div>

    <div v-if="chatStore.error" class="error-banner">
      <span>⚠️</span> {{ chatStore.error }}
    </div>

    <div class="input-area">
      <textarea
        v-model="query"
        placeholder="输入您的问题，Ctrl+Enter 或回车发送..."
        @keydown.enter.ctrl.prevent="sendMessage"
        @keydown.enter.exact.prevent="sendMessage"
        @input="autoResize"
        rows="1"
      />
      <button v-if="chatStore.isLoading" @click="chatStore.stopGeneration()" class="stop-btn">
        ⏹ 停止
      </button>
      <button v-else @click="sendMessage" class="send-btn">
        发送
      </button>
    </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, nextTick } from 'vue'
import { useChatStore } from '@/stores/chat'
import { renderMarkdown } from '@/utils/markdown'
import { extractError } from '@/utils/error'
import CitationPanel from '@/components/CitationPanel.vue'
import FeedbackButton from '@/components/FeedbackButton.vue'
import HighRiskWarning from '@/components/HighRiskWarning.vue'

const chatStore = useChatStore()
const query = ref('')
const messagesEl = ref<HTMLElement | null>(null)

// 会话侧栏
onMounted(() => {
  chatStore.loadThreads()
})

function newConversation() {
  chatStore.newConversation()
  scrollToBottom()
}

async function switchThread(id: string) {
  if (chatStore.threadId === id) return
  await chatStore.switchThread(id)
  scrollToBottom()
}

async function removeThread(id: string) {
  if (!confirm('删除这个会话及其全部消息？')) return
  await chatStore.removeThread(id)
}

function renderContent(message: any): string {
  if (!message.content) return '<span class="placeholder">思考中...</span>'
  // 用户消息纯文本转义
  if (message.role === 'user') return escapeHtml(message.content)
  // 流式中（最后一条 assistant 且正在生成）：转义纯文本 + 打字光标，
  // 避免每个 token 都整段重渲染 Markdown（性能 + 不闪烁）
  if (isStreaming(message)) {
    return escapeHtml(message.content) + '<span class="typing-cursor"></span>'
  }
  // 流式结束：一次性渲染 Markdown
  return renderMarkdown(message.content)
}

function isLastMessage(message: any): boolean {
  const arr = chatStore.messages
  return arr.length > 0 && arr[arr.length - 1].id === message.id
}

function isStreaming(message: any): boolean {
  return chatStore.isLoading && isLastMessage(message)
}

async function copyMessage(message: any) {
  try {
    await navigator.clipboard.writeText(message.content || '')
  } catch {
    // 剪贴板不可用时静默失败
  }
}

function resend() {
  if (chatStore.lastQuery && !chatStore.isLoading) {
    query.value = chatStore.lastQuery
    sendMessage()
  }
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function autoResize(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}

function sendMessage() {
  if (!query.value.trim() || chatStore.isLoading) return
  const q = query.value
  query.value = ''
  chatStore
    .sendMessage(q)
    .then(() => scrollToBottom())
    .catch((err) => {
      // 兜底（G6.3）：streamChat 回调内异常时走统一错误提取，避免未处理 rejection
      chatStore.error = extractError(err)
    })
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    }
  })
}
</script>

<style scoped>
.chat-layout {
  display: flex;
  height: calc(100vh - 64px);
  max-width: 1100px;
  margin: 0 auto;
}

.chat-view {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.thread-sidebar {
  width: 240px;
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  background: var(--card);
  padding: 14px 12px;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  overflow-y: auto;
}

.new-chat-btn {
  padding: 0.6rem;
  border: 1px solid var(--primary);
  background: #fff;
  color: var(--primary);
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.9rem;
  transition: all 0.2s;
}
.new-chat-btn:hover {
  background: var(--primary-soft);
}

.no-threads {
  color: #aaa;
  font-size: 0.85rem;
  text-align: center;
  padding: 1rem 0;
}

.thread-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.thread-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.55rem 0.6rem;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.85rem;
  color: #555;
}
.thread-item:hover {
  background: #f0f0f0;
}
.thread-item.active {
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 500;
}

.thread-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.thread-delete {
  border: none;
  background: transparent;
  color: #bbb;
  cursor: pointer;
  font-size: 0.8rem;
  padding: 0.1rem 0.3rem;
  border-radius: 4px;
}
.thread-delete:hover {
  color: var(--danger);
  background: var(--danger-soft);
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 1.5rem 1rem;
  scroll-behavior: smooth;
}

/* 空状态 */
.empty-state {
  text-align: center;
  margin-top: 20vh;
  color: #999;
}
.empty-icon {
  font-size: 4rem;
  margin-bottom: 1rem;
}
.empty-state h2 {
  color: #555;
  margin-bottom: 0.5rem;
}
.empty-state p {
  font-size: 0.95rem;
}

/* 消息气泡 */
.message {
  display: flex;
  gap: 0.75rem;
  margin-bottom: 1.25rem;
  animation: fadeIn 0.2s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

.message-avatar {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.2rem;
  background: #f0f0f0;
}

.message.user .message-avatar {
  background: var(--primary-soft);
}

.message-body {
  flex: 1;
  max-width: calc(100% - 48px);
}

.message-content {
  padding: 0.85rem 1rem;
  border-radius: 12px;
  line-height: 1.6;
  font-size: 0.95rem;
  word-break: break-word;
}

.message.user .message-content {
  background: var(--primary-soft);
  color: #0d47a1;
  border-top-right-radius: 4px;
}

.message.assistant .message-content {
  background: #f8f9fa;
  border: 1px solid #eee;
  border-top-left-radius: 4px;
}

/* Markdown 内容样式 */
.message-content :deep(p) { margin: 0.4em 0; }
.message-content :deep(p:first-child) { margin-top: 0; }
.message-content :deep(p:last-child) { margin-bottom: 0; }
.message-content :deep(h1),
.message-content :deep(h2),
.message-content :deep(h3) {
  margin: 0.6em 0 0.3em;
  font-size: 1.05em;
  color: #333;
}
.message-content :deep(ul),
.message-content :deep(ol) {
  padding-left: 1.4em;
  margin: 0.4em 0;
}
.message-content :deep(li) { margin: 0.2em 0; }
.message-content :deep(code) {
  background: #eee;
  padding: 0.1em 0.3em;
  border-radius: 3px;
  font-size: 0.9em;
}
.message-content :deep(pre) {
  background: #2d2d2d;
  color: #f8f8f2;
  padding: 0.75rem;
  border-radius: 8px;
  overflow-x: auto;
  margin: 0.5em 0;
}
.message-content :deep(pre code) {
  background: transparent;
  color: inherit;
  padding: 0;
}
.message-content :deep(blockquote) {
  border-left: 3px solid var(--primary);
  padding-left: 0.75rem;
  color: #666;
  margin: 0.5em 0;
}

.placeholder {
  color: #999;
  font-style: italic;
}

.warning-area {
  margin-top: 0.5rem;
}

.message-footer {
  margin-top: 0.4rem;
  display: flex;
  justify-content: flex-end;
}

/* 打字指示 */
.typing {
  display: flex;
  gap: 4px;
  padding: 1rem 0 1rem 48px;
}
.typing span {
  width: 8px;
  height: 8px;
  background: #ccc;
  border-radius: 50%;
  animation: bounce 1.2s infinite;
}
.typing span:nth-child(2) { animation-delay: 0.2s; }
.typing span:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce {
  0%, 60%, 100% { transform: translateY(0); }
  30% { transform: translateY(-6px); }
}

.error-banner {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  background: #fff3f3;
  color: var(--danger);
  padding: 0.6rem 1rem;
  border-top: 1px solid #ffcdd2;
  font-size: 0.9rem;
}

.input-area {
  display: flex;
  gap: 0.75rem;
  padding: 1rem;
  border-top: 1px solid var(--border);
  background: #fff;
}

textarea {
  flex: 1;
  padding: 0.7rem 1rem;
  border: 1.5px solid #ddd;
  border-radius: 12px;
  resize: none;
  font-size: 0.95rem;
  line-height: 1.5;
  max-height: 160px;
  outline: none;
  transition: border-color 0.2s;
}
textarea:focus {
  border-color: var(--primary);
}

.send-btn {
  padding: 0.7rem 1.5rem;
  background: linear-gradient(135deg, var(--primary), var(--primary-hover));
  color: white;
  border: none;
  border-radius: 12px;
  cursor: pointer;
  font-size: 0.95rem;
  font-weight: 500;
  transition: all 0.2s;
  white-space: nowrap;
}
.send-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(25, 118, 210, 0.3);
}
.send-btn:disabled {
  background: #bdbdbd;
  cursor: not-allowed;
}

.stop-btn {
  padding: 0.7rem 1.5rem;
  background: var(--danger-soft);
  color: var(--danger);
  border: 1px solid #fecaca;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 0.95rem;
  font-weight: 500;
  transition: all 0.2s;
}
.stop-btn:hover {
  background: var(--danger);
  color: #fff;
  border-color: var(--danger);
}

/* 消息操作：复制 / 重试 / 反馈 */
.footer-btn {
  border: none;
  background: transparent;
  color: var(--text-3);
  cursor: pointer;
  font-size: 12px;
  padding: 2px 6px;
  border-radius: 6px;
  transition: all 0.15s ease;
  font-family: var(--font);
}
.footer-btn:hover {
  color: var(--primary);
  background: var(--primary-soft);
}

/* 打字光标 */
.typing-cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  background: var(--primary);
  vertical-align: text-bottom;
  margin-left: 2px;
  animation: blink 1s step-end infinite;
}
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
</style>

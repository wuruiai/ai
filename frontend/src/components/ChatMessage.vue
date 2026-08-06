<template>
  <div :class="['chat-message', message.role]">
    <div class="message-content">{{ message.content }}</div>
    <div v-if="message.citations?.length" class="citations">
      <CitationPanel :citations="message.citations" />
    </div>
  </div>
</template>

<script setup lang="ts">
import CitationPanel from './CitationPanel.vue'

defineProps<{
  message: {
    id: string
    role: 'user' | 'assistant'
    content: string
    citations?: any[]
  }
}>()
</script>

<style scoped>
.chat-message {
  margin-bottom: 1rem;
  padding: 1rem;
  border-radius: 8px;
}

.chat-message.user {
  background: #e3f2fd;
  margin-left: 20%;
}

.chat-message.assistant {
  background: #f5f5f5;
  margin-right: 20%;
}
</style>

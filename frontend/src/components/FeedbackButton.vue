<template>
  <div class="feedback-button">
    <button
      type="button"
      @click="submitFeedback('helpful')"
      :disabled="submitted"
      :class="{ active: rating === 'helpful' }"
    >
      👍 有帮助
    </button>
    <button
      type="button"
      @click="submitFeedback('not_helpful')"
      :disabled="submitted"
      :class="{ active: rating === 'not_helpful' }"
    >
      👎 无帮助
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { submitFeedback as apiSubmitFeedback } from '@/api/feedback'

const props = defineProps<{
  messageId: string
}>()

const submitted = ref(false)
const rating = ref<string | null>(null)

async function submitFeedback(r: string) {
  if (submitted.value) return

  rating.value = r
  submitted.value = true
  try {
    await apiSubmitFeedback(props.messageId, r)
  } catch (error) {
    console.error('Feedback failed:', error)
    submitted.value = false
    rating.value = null
  }
}
</script>

<style scoped>
.feedback-button {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

button {
  padding: 4px 12px;
  border: 1px solid var(--border);
  background: var(--card);
  border-radius: 999px;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-2);
  transition: all 0.15s ease;
  font-family: var(--font);
}

button:hover:not(:disabled) {
  border-color: var(--primary);
  color: var(--primary);
}

button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

button.active {
  background: var(--primary-soft);
  border-color: var(--primary);
  color: var(--primary);
}
</style>

<script setup lang="ts">
/**
 * 执行状态提示。默认带标题、原因和最后执行的步骤（执行明细里用）；
 * compact 用在聊天里：只是一行安静的小字（没有底色和图标），不重复标题，也不带细节。
 */
import { CircleAlert, CircleHelp } from '@lucide/vue'
import { computed } from 'vue'

import type { UiMessage } from '@/composables/chatModel'
import { executionNotice } from '@/composables/executionStatus'

const props = defineProps<{ message: UiMessage; compact?: boolean }>()
const notice = computed(() => executionNotice(props.message))
const compactText = computed(() => {
  const current = notice.value
  if (!current) return ''
  if (current.status === 'cancelled') return '已停止'
  if (current.status === 'interrupted') return '已中断'
  return current.summary
})
const tone = computed(() => notice.value?.tone === 'error' ? 'bg-error/5 text-error' : notice.value?.tone === 'info' ? 'bg-info/5 text-ink' : 'bg-warning/10 text-[#805719]')
</script>

<template>
  <p v-if="notice && compact" class="min-w-0 break-words text-[13px] leading-6 [overflow-wrap:anywhere]" :class="notice.tone === 'error' ? 'text-error' : 'text-faint'" role="status" aria-live="polite">{{ compactText }}</p>
  <div v-else-if="notice" class="flex min-w-0 gap-2 rounded-xl px-3.5 py-2.5 text-[13px] leading-6" :class="tone" role="status" aria-live="polite">
    <CircleHelp v-if="notice.tone === 'info'" class="mt-1 size-4 shrink-0" />
    <CircleAlert v-else class="mt-1 size-4 shrink-0" />
    <div class="min-w-0 break-words [overflow-wrap:anywhere]">
      <p class="font-medium">{{ notice.title }}</p>
      <p>{{ notice.summary }}</p>
      <p v-if="notice.lastStep" class="mt-1 text-xs opacity-80">最后执行：{{ notice.lastStep }}</p>
    </div>
  </div>
</template>

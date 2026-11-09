<script setup lang="ts">
import { CircleAlert, CircleHelp } from '@lucide/vue'
import { computed } from 'vue'

import type { UiMessage } from '@/composables/chatModel'
import { executionNotice } from '@/composables/executionStatus'

const props = defineProps<{ message: UiMessage }>()
const notice = computed(() => executionNotice(props.message))
</script>

<template>
  <div v-if="notice" class="flex min-w-0 gap-2 rounded-xl px-3.5 py-2.5 text-[13px] leading-6" :class="notice.tone === 'error' ? 'bg-error/5 text-error' : notice.tone === 'info' ? 'bg-info/5 text-ink' : 'bg-warning/10 text-[#805719]'" role="status" aria-live="polite">
    <CircleHelp v-if="notice.tone === 'info'" class="mt-1 size-4 shrink-0" />
    <CircleAlert v-else class="mt-1 size-4 shrink-0" />
    <div class="min-w-0 break-words [overflow-wrap:anywhere]">
      <p class="font-medium">{{ notice.title }}</p>
      <p>{{ notice.summary }}</p>
      <p v-if="notice.lastStep" class="mt-1 text-xs opacity-80">最后执行：{{ notice.lastStep }}</p>
    </div>
  </div>
</template>

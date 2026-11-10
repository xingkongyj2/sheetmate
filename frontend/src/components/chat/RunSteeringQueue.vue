<script setup lang="ts">
import { Check, Clock3, LoaderCircle, X } from '@lucide/vue'
import { computed } from 'vue'

import type { RunSteer } from '@/api/types'

const props = defineProps<{ items: RunSteer[]; withdrawing?: number | null }>()
const emit = defineEmits<{ withdraw: [messageId: number] }>()
const visible = computed(() => props.items.filter((item) => item.status !== 'withdrawn'))
</script>

<template>
  <ul v-if="visible.length" class="mb-3 max-h-52 space-y-2 overflow-y-auto" aria-label="追加要求" aria-live="polite">
    <li v-for="item in visible" :key="item.message_id" class="flex items-start gap-2 rounded-xl border border-line bg-paper/50 px-3 py-2.5 text-xs" :data-steer-status="item.status">
      <Check v-if="item.status === 'committed'" class="mt-0.5 size-3.5 shrink-0 text-success" aria-hidden="true" />
      <Clock3 v-else class="mt-0.5 size-3.5 shrink-0 text-mute" aria-hidden="true" />
      <div class="min-w-0 flex-1">
        <p class="font-medium" :class="item.status === 'committed' ? 'text-success' : 'text-mute'">{{ item.status === 'committed' ? '已采纳' : '等待采纳' }}</p>
        <p class="mt-0.5 whitespace-pre-wrap break-words leading-5 text-ink [overflow-wrap:anywhere]">{{ item.content }}</p>
      </div>
      <button v-if="item.status === 'queued'" type="button" class="icon-btn -mr-1 -mt-1 size-7 shrink-0 disabled:opacity-50" :disabled="withdrawing === item.message_id" :aria-label="`撤回追加要求：${item.content}`" title="撤回" @click="emit('withdraw', item.message_id)">
        <LoaderCircle v-if="withdrawing === item.message_id" class="size-3.5 animate-spin" />
        <X v-else class="size-3.5" />
      </button>
    </li>
  </ul>
</template>

<script setup lang="ts">
/** 文件行里的脱敏按钮：图标表示识别状态（说明文字在 title 和 aria-label 里），点击打开这个文件的脱敏维护（手动选择哪些列脱敏）。
 *  没有状态（不是表格文件）就什么都不显示。 */
import { LoaderCircle, Shield, ShieldAlert, ShieldCheck } from '@lucide/vue'
import { computed, ref } from 'vue'

import { fileApi } from '@/api'
import type { PrivacyFileStatus } from '@/api/types'
import { privacyStatusText } from '@/composables/privacyPresentation'

import PrivacyColumnsDialog from './PrivacyColumnsDialog.vue'

const props = defineProps<{ status?: PrivacyFileStatus | null; file: { file_id: string; filename: string }; conversationId?: string }>()
const open = ref(false)
const live = ref<PrivacyFileStatus | null>(null)       // 在弹窗里改过之后拉到的最新状态（列表里的状态是缓存的）
const shown = computed(() => live.value ?? props.status ?? null)
const text = computed(() => (shown.value ? privacyStatusText(shown.value) : ''))

async function refresh() {
  try {
    live.value = (await fileApi.privacyStatus([props.file.file_id]))[props.file.file_id] ?? live.value
  } catch {
    // 静默：保持原来的图标
  }
}
async function close() {
  open.value = false
  await refresh()
}
</script>

<template>
  <button
    v-if="shown" type="button" class="icon-btn" :aria-label="`${text}，设置 ${file.filename} 的脱敏`" :title="text" aria-haspopup="dialog"
    data-testid="privacy-status" :data-state="shown.state" @click="open = true"
  >
    <LoaderCircle v-if="shown.state === 'running'" class="size-4 animate-spin" aria-hidden="true" />
    <ShieldAlert v-else-if="shown.state === 'failed'" class="size-4 text-[#b7770c]" aria-hidden="true" />
    <ShieldCheck v-else-if="shown.state === 'ready'" class="size-4" :class="shown.columns > 0 ? 'text-[#318b76]' : 'text-faint'" aria-hidden="true" />
    <Shield v-else class="size-4 text-faint/60" stroke-dasharray="2.5 2.5" aria-hidden="true" />
  </button>
  <PrivacyColumnsDialog v-if="open" :file-id="file.file_id" :filename="file.filename" :conversation-id="conversationId" @close="close" @changed="refresh" />
</template>

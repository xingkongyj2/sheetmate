<script setup lang="ts">
/** 文件行里的脱敏小图标：只展示，不可点击；说明文字放在 title 和 aria-label 里。没有状态（不是表格文件）就什么都不显示。 */
import { LoaderCircle, Shield, ShieldAlert, ShieldCheck } from '@lucide/vue'
import { computed } from 'vue'

import type { PrivacyFileStatus } from '@/api/types'
import { privacyStatusText } from '@/composables/privacyPresentation'

const props = defineProps<{ status?: PrivacyFileStatus | null }>()
const text = computed(() => (props.status ? privacyStatusText(props.status) : ''))
</script>

<template>
  <span v-if="status" class="inline-flex size-5 shrink-0 items-center justify-center" role="img" :aria-label="text" :title="text" data-testid="privacy-status" :data-state="status.state">
    <LoaderCircle v-if="status.state === 'running'" class="size-3.5 animate-spin text-mute" aria-hidden="true" />
    <ShieldAlert v-else-if="status.state === 'failed'" class="size-3.5 text-[#b7770c]" aria-hidden="true" />
    <ShieldCheck v-else-if="status.state === 'ready'" class="size-3.5" :class="status.columns > 0 ? 'text-[#318b76]' : 'text-faint'" aria-hidden="true" />
    <Shield v-else class="size-3.5 text-faint/60" stroke-dasharray="2.5 2.5" aria-hidden="true" />
  </span>
</template>

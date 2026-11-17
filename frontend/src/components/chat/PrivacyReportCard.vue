<script setup lang="ts">
/** 助手回复的第一块内容：本轮对话开始前对文件做了什么脱敏。进行中只显示一行，完成后一句话加可展开的明细。 */
import { ChevronRight, LoaderCircle, ShieldAlert, ShieldCheck } from '@lucide/vue'
import { computed } from 'vue'

import type { PrivacyReport } from '@/api/types'
import { protectedColumnCount, runningPrivacyText } from '@/composables/privacyPresentation'

import PrivacyFiles from './PrivacyFiles.vue'

const props = defineProps<{ report?: PrivacyReport; running?: string[] }>()
const blocked = computed(() => props.report?.status === 'blocked')
// 被中止时只有真的识别出了列才值得展开；否则没有任何可看的内容
const expandable = computed(() => !!props.report && props.report.files.some((file) => protectedColumnCount(file) > 0))
// 只列真的脱敏了的文件：没有需要替换的文件不展示
const shownFiles = computed(() => (props.report?.files ?? []).filter((file) => protectedColumnCount(file) > 0))
</script>

<template>
  <section class="privacy-card text-[13px] leading-5" aria-label="脱敏处理" data-testid="privacy-report">
    <div v-if="!report" class="flex items-start gap-1.5 py-0.5 text-mute" role="status" aria-live="polite" data-testid="privacy-running">
      <LoaderCircle class="mt-[3px] size-3.5 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      <span class="min-w-0 flex-1 break-words">{{ runningPrivacyText(running ?? []) }}…</span>
    </div>

    <template v-else>
      <details class="group/privacy">
        <summary
          class="flex w-fit max-w-full list-none items-start gap-1.5 rounded py-1 text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink [&::-webkit-details-marker]:hidden"
          :class="expandable ? 'cursor-pointer' : 'cursor-default'"
          @click="!expandable && $event.preventDefault()"
        >
          <ShieldAlert v-if="blocked" class="mt-[3px] size-3.5 shrink-0 text-error" aria-hidden="true" />
          <ShieldCheck v-else class="mt-[3px] size-3.5 shrink-0 text-[#318b76]" aria-hidden="true" />
          <span class="min-w-0 break-words [overflow-wrap:anywhere]" :class="blocked ? 'font-medium text-error' : ''">
            {{ blocked ? '脱敏没有完成，已停止处理，没有任何数据发给 AI。' : report.headline }}
          </span>
          <ChevronRight v-if="expandable" class="mt-[3px] size-3.5 shrink-0 text-faint transition group-open/privacy:rotate-90" aria-hidden="true" />
        </summary>
        <div v-if="expandable" class="ml-5 mt-1.5 space-y-2">
          <PrivacyFiles :files="shownFiles" :unfinished="blocked" open-first />
        </div>
      </details>
      <div class="ml-5 space-y-0.5">
        <p v-if="blocked && report.reason" class="break-words text-xs leading-5 text-mute" role="alert">{{ report.reason }}</p>
        <p v-for="note in report.notes" :key="note" class="break-words text-xs leading-5 text-faint">{{ note }}</p>
      </div>
    </template>
  </section>
</template>

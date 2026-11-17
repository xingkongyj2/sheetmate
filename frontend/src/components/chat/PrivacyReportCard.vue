<script setup lang="ts">
/** 助手回复的第一块内容：本轮对话开始前对文件做了什么脱敏。
 *  收起：一句话总结。展开：每个文件一张表——左上文件名、右上查看 / 下载；下面按工作表分 tab，表里是脱敏了哪些字段、多少个值。 */
import { ChevronRight, Download, Eye, FileSpreadsheet, LoaderCircle, ShieldAlert, ShieldCheck } from '@lucide/vue'
import { computed, reactive, ref } from 'vue'

import type { PrivacyFile, PrivacyReport, PrivacySheet } from '@/api/types'
import { formatCount, protectedColumnCount, runningPrivacyText } from '@/composables/privacyPresentation'
import { usePrivacyCopy } from '@/composables/usePrivacyCopy'

import PrivacyCopyDialog from './PrivacyCopyDialog.vue'

const props = defineProps<{ report?: PrivacyReport; running?: string[] }>()
const blocked = computed(() => props.report?.status === 'blocked')
// 只列真的脱敏了的文件、真的有脱敏列的工作表
const shownFiles = computed(() => (props.report?.files ?? []).filter((file) => protectedColumnCount(file) > 0)
  .map((file) => ({ ...file, sheets: file.sheets.filter((sheet) => sheet.columns.length) })))
const expandable = computed(() => shownFiles.value.length > 0)
const sum = (sheet: PrivacySheet) => sheet.columns.reduce((count, column) => count + column.values, 0)
const totalValues = computed(() => shownFiles.value.reduce((total, file) => total + file.sheets.reduce((inner, sheet) => inner + sum(sheet), 0), 0))
const headline = computed(() => blocked.value ? '脱敏没有完成，已停止处理，没有任何数据发给 AI。'
  : `已脱敏 ${shownFiles.value.length} 个文件 · ${formatCount(totalValues.value)} 个值`)

const picked = reactive<Record<string, string>>({})        // 每个文件当前看的工作表
function activeSheet(file: PrivacyFile): PrivacySheet | undefined {
  return file.sheets.find((sheet) => sheet.sheet === picked[file.file_id]) ?? file.sheets[0]
}

const { downloading, download } = usePrivacyCopy()
const viewing = ref<{ fileId: string; filename: string } | null>(null)
</script>

<template>
  <section class="privacy-card" aria-label="脱敏处理" data-testid="privacy-report">
    <div v-if="!report" class="flex items-start gap-1.5 py-1 text-[15px] leading-6 text-mute" role="status" aria-live="polite" data-testid="privacy-running">
      <LoaderCircle class="mt-1 size-4 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      <span class="min-w-0 flex-1 break-words">{{ runningPrivacyText(running ?? []) }}…</span>
    </div>

    <template v-else>
      <details class="group/privacy">
        <summary
          class="flex w-fit max-w-full list-none items-start gap-1.5 rounded py-1 text-[15px] leading-6 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink [&::-webkit-details-marker]:hidden"
          :class="expandable ? 'cursor-pointer' : 'cursor-default'"
          @click="!expandable && $event.preventDefault()"
        >
          <ShieldAlert v-if="blocked" class="mt-1 size-4 shrink-0 text-error" aria-hidden="true" />
          <ShieldCheck v-else class="mt-1 size-4 shrink-0 text-[#318b76]" aria-hidden="true" />
          <span class="min-w-0 break-words [overflow-wrap:anywhere]" :class="blocked ? 'font-medium text-error' : 'text-ink'">{{ headline }}</span>
          <ChevronRight v-if="expandable" class="mt-1 size-4 shrink-0 text-faint transition group-open/privacy:rotate-90" aria-hidden="true" />
        </summary>

        <div v-if="expandable" class="mb-1 mt-1.5 max-w-xl space-y-2.5">
          <article v-for="file in shownFiles" :key="file.file_id || file.filename" class="overflow-hidden rounded-xl border border-line bg-white">
            <header class="flex min-w-0 items-center gap-2 px-3.5 pb-2 pt-2.5">
              <FileSpreadsheet class="size-4 shrink-0 text-fun-green" aria-hidden="true" />
              <span class="min-w-0 flex-1 truncate text-[13px] font-medium text-ink" :title="file.filename">{{ file.filename }}</span>
              <template v-if="!blocked && file.file_id">
                <button type="button" class="privacy-action" title="查看 AI 看到的样子" :aria-label="`查看 ${file.filename} 脱敏后的样子`" @click="viewing = { fileId: file.file_id, filename: file.filename }">
                  <Eye class="size-4" />
                </button>
                <button type="button" class="privacy-action" :disabled="!!downloading" title="下载脱敏副本" :aria-label="`下载 ${file.filename} 的脱敏副本`" @click="download(file.file_id, file.filename)">
                  <LoaderCircle v-if="downloading === file.file_id" class="size-4 animate-spin motion-reduce:animate-none" />
                  <Download v-else class="size-4" />
                </button>
              </template>
            </header>

            <div class="flex gap-1 overflow-x-auto px-3 pb-2" role="tablist" :aria-label="`${file.filename} 的工作表`">
              <button
                v-for="sheet in file.sheets"
                :key="sheet.sheet"
                type="button"
                role="tab"
                class="privacy-tab"
                :class="{ 'is-active': activeSheet(file) === sheet }"
                :aria-selected="activeSheet(file) === sheet"
                :title="sheet.sheet"
                @click="picked[file.file_id] = sheet.sheet"
              >
                <span class="max-w-40 truncate">{{ sheet.sheet }}</span>
              </button>
            </div>

            <table v-if="activeSheet(file)" class="w-full border-t border-line text-[13px] leading-5" role="tabpanel">
              <thead>
                <tr class="text-left text-xs text-faint">
                  <th class="px-3.5 py-1.5 font-normal">字段</th>
                  <th class="px-3.5 py-1.5 font-normal">类型</th>
                  <th class="px-3.5 py-1.5 text-right font-normal">脱敏值数</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="column in activeSheet(file)!.columns" :key="column.name" class="border-t border-line/70" :title="column.reason || undefined">
                  <td class="break-all px-3.5 py-2 font-medium text-ink">{{ column.name }}</td>
                  <td class="px-3.5 py-2 text-mute">{{ column.kind_label }}</td>
                  <td class="px-3.5 py-2 text-right tabular-nums text-ink">{{ formatCount(column.values) }}</td>
                </tr>
              </tbody>
            </table>
          </article>
        </div>
      </details>

      <div v-if="(blocked && report.reason) || report.notes.length" class="space-y-0.5">
        <p v-if="blocked && report.reason" class="break-words text-xs leading-5 text-mute" role="alert">{{ report.reason }}</p>
        <p v-for="note in report.notes" :key="note" class="break-words text-xs leading-5 text-faint">{{ note }}</p>
      </div>
    </template>
    <PrivacyCopyDialog v-if="viewing" :file-id="viewing.fileId" :filename="viewing.filename" @close="viewing = null" />
  </section>
</template>

<style scoped>
.privacy-action {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 1.75rem;
  height: 1.75rem;
  border-radius: 0.5rem;
  color: var(--color-mute);
  transition: color 150ms, background-color 150ms;
}
.privacy-action:hover:not(:disabled) {
  color: var(--color-ink);
  background: var(--color-paper);
}
.privacy-action:disabled {
  cursor: default;
  opacity: 0.6;
}

.privacy-tab {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 0.375rem;
  border-radius: 9999px;
  padding: 0.125rem 0.625rem;
  font-size: 0.75rem;
  line-height: 1.25rem;
  color: var(--color-mute);
  transition: color 150ms, background-color 150ms;
}
.privacy-tab:hover {
  color: var(--color-ink);
  background: var(--color-paper);
}
.privacy-tab.is-active {
  color: #fff;
  background: var(--color-ink);
}
</style>

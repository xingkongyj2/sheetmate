<script setup lang="ts">
/** 脱敏明细：文件 → 工作表 → 被保护的列（类型、处理方式、依据、来源、替换数）。聊天卡片和详情面板共用。 */
import { ChevronRight, Download, Eye, FileSpreadsheet, LoaderCircle, Table2 } from '@lucide/vue'
import { ref } from 'vue'

import type { PrivacyFile, PrivacyFileState } from '@/api/types'
import { formatCount, PRIVACY_SOURCE_LABELS, protectedColumnCount } from '@/composables/privacyPresentation'
import { usePrivacyCopy } from '@/composables/usePrivacyCopy'

import PrivacyCopyDialog from './PrivacyCopyDialog.vue'

/** unfinished：识别没有完成（脱敏被中止），不能写成「没有发现联系方式或地址」 */
const props = defineProps<{ files: (PrivacyFile | PrivacyFileState)[]; openFirst?: boolean; unfinished?: boolean }>()

const { downloading, download } = usePrivacyCopy()
const viewing = ref<{ fileId: string; filename: string } | null>(null)

function status(file: PrivacyFile | PrivacyFileState) {
  return 'status' in file ? file.status : 'ready'
}
function error(file: PrivacyFile | PrivacyFileState) {
  return 'error' in file ? file.error : null
}
/** 有被保护的列、识别已完成的文件才能看脱敏副本；脱敏被中止（没有任何数据发给 AI）时没有「AI 看到的样子」可看 */
function canCopy(file: PrivacyFile | PrivacyFileState) {
  return !props.unfinished && !!file.file_id && status(file) === 'ready' && protectedColumnCount(file) > 0
}
</script>

<template>
  <ul class="space-y-1.5" aria-label="脱敏的文件">
    <li v-for="(file, index) in files" :key="file.file_id || file.filename" class="@container/file min-w-0">
      <details class="group/file rounded-xl border border-line bg-white" :open="openFirst && index === 0">
        <summary class="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-[13px] [&::-webkit-details-marker]:hidden">
          <ChevronRight class="size-3.5 shrink-0 text-faint transition group-open/file:rotate-90" aria-hidden="true" />
          <FileSpreadsheet class="size-4 shrink-0 text-fun-green" aria-hidden="true" />
          <span class="min-w-0 flex-1 truncate font-medium" :title="file.filename">{{ file.filename }}</span>
          <LoaderCircle v-if="status(file) === 'pending'" class="size-3.5 shrink-0 animate-spin text-mute" aria-label="识别中" />
          <span v-else-if="status(file) === 'failed'" class="tag shrink-0 !bg-error/10 !text-error">识别失败</span>
          <!-- 很窄时（比如面板拖到最窄）这行小字让位给文件名，展开后的明细里本来就有 -->
          <span v-else class="hidden shrink-0 text-[11px] tabular-nums text-faint @[300px]/file:inline" :class="{ '!inline': !canCopy(file) }">{{ protectedColumnCount(file) ? `${protectedColumnCount(file)} 列已保护` : unfinished ? '未完成识别' : '无需替换' }}</span>
          <!-- 按钮在 summary 里：点击不能触发展开/收起 -->
          <template v-if="canCopy(file)">
            <button type="button" class="icon-btn size-7 shrink-0" title="查看 AI 看到的样子" :aria-label="`查看 ${file.filename} 脱敏后的样子`" @click.stop.prevent="viewing = { fileId: file.file_id, filename: file.filename }">
              <Eye class="size-4" />
            </button>
            <button type="button" class="icon-btn size-7 shrink-0" :disabled="!!downloading" title="下载脱敏副本" :aria-label="`下载 ${file.filename} 的脱敏副本`" @click.stop.prevent="download(file.file_id, file.filename)">
              <LoaderCircle v-if="downloading === file.file_id" class="size-4 animate-spin motion-reduce:animate-none" />
              <Download v-else class="size-4" />
            </button>
          </template>
        </summary>
        <div class="space-y-3 border-t border-line px-3 py-2.5">
          <p v-if="status(file) === 'pending'" class="text-xs leading-5 text-faint">正在识别敏感列…</p>
          <p v-if="error(file)" class="break-words text-xs leading-5 text-error" role="alert">{{ error(file) }}</p>
          <p v-if="file.cached" class="text-[11px] leading-5 text-faint">{{ file.source === 'upload' ? '上传时已识别' : '沿用之前的识别结果' }}</p>
          <p v-if="!file.sheets.length && status(file) === 'ready'" class="text-xs leading-5 text-faint">{{ unfinished ? '识别没有完成' : '没有发现联系方式或地址' }}</p>
          <section v-for="sheet in file.sheets" :key="sheet.sheet" class="min-w-0">
            <div class="mb-1.5 flex min-w-0 items-center gap-1.5 text-[11px] text-faint">
              <Table2 class="size-3 shrink-0" aria-hidden="true" />
              <span class="min-w-0 truncate" :title="sheet.sheet">{{ sheet.sheet }}</span>
              <span class="shrink-0 tabular-nums">· {{ formatCount(sheet.rows) }} 行</span>
            </div>
            <p v-if="!sheet.columns.length" class="text-xs leading-5 text-faint">{{ unfinished ? '识别没有完成' : '没有发现联系方式或地址' }}</p>
            <ul v-else class="space-y-2.5">
              <li v-for="column in sheet.columns" :key="column.name" class="min-w-0">
                <div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                  <span class="break-all font-mono text-[13px] font-medium">{{ column.name }}</span>
                  <span class="tag">{{ column.kind_label }}</span>
                  <span class="tag" :class="column.source === 'rules' ? '' : '!bg-[#6265DC]/10 !text-[#6265DC]'" :title="`识别来源：${PRIVACY_SOURCE_LABELS[column.source]}`">{{ PRIVACY_SOURCE_LABELS[column.source] }}</span>
                  <span class="ml-auto shrink-0 text-[11px] tabular-nums text-faint">{{ formatCount(column.values) }} 个值</span>
                </div>
                <p class="mt-0.5 break-words text-xs leading-5 text-mute">{{ column.reason ? `${column.action_label} · ${column.reason}` : column.action_label }}</p>
              </li>
            </ul>
          </section>
        </div>
      </details>
    </li>
  </ul>
  <PrivacyCopyDialog v-if="viewing" :file-id="viewing.fileId" :filename="viewing.filename" @close="viewing = null" />
</template>

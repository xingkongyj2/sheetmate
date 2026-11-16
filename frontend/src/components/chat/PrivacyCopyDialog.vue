<script setup lang="ts">
/** 脱敏副本：AI 实际看到的样子。被保护的列显示为令牌，真实内容留在本机；可以翻页、换工作表、下载。 */
import { ChevronLeft, ChevronRight, Download, LoaderCircle, RotateCw } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { fileApi } from '@/api'
import { errorText } from '@/api/http'
import type { PrivacyCopyPreview } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import { formatCount } from '@/composables/privacyPresentation'
import { usePrivacyCopy } from '@/composables/usePrivacyCopy'

const PAGE_SIZE = 50
const props = defineProps<{ fileId: string; filename: string }>()
const emit = defineEmits<{ close: [] }>()

const { conversationId, downloading, download } = usePrivacyCopy()
const preview = ref<PrivacyCopyPreview | null>(null)
const loading = ref(true)
const error = ref('')
let version = 0
let last: { sheet?: string; offset: number } = { offset: 0 }

async function load(sheet?: string, offset = 0) {
  const current = ++version
  last = { sheet, offset }
  loading.value = true
  error.value = ''
  try {
    const data = await fileApi.privacyPreview(props.fileId, { sheet, offset, limit: PAGE_SIZE, conversationId: conversationId.value })
    if (current !== version) return
    preview.value = data
  } catch (err) {
    if (current === version) error.value = errorText(err)
  } finally {
    if (current === version) loading.value = false
  }
}
onMounted(() => { void load() })

const hasPrev = computed(() => !!preview.value && preview.value.offset > 0)
const hasNext = computed(() => !!preview.value && preview.value.offset + preview.value.rows.length < preview.value.total)
const range = computed(() => {
  const data = preview.value
  if (!data || !data.total) return '没有数据'
  return `第 ${formatCount(data.offset + 1)}–${formatCount(data.offset + data.rows.length)} 行，共 ${formatCount(data.total)} 行`
})
const protectedCount = computed(() => preview.value?.columns.filter((column) => column.protected).length ?? 0)

function page(step: number) {
  const data = preview.value
  if (!data || loading.value) return
  void load(data.sheet, Math.max(0, data.offset + step * PAGE_SIZE))
}
function pickSheet(sheet: string) {
  if (loading.value || sheet === preview.value?.sheet) return
  void load(sheet, 0)
}
function cell(value: string | number | null): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : String(Math.round(value * 10000) / 10000)
  return value
}
</script>

<template>
  <BaseModal width="max-w-5xl" @close="emit('close')">
    <template #title>
      <div class="min-w-0">
        <div class="flex min-w-0 items-center gap-1.5">
          <h3 class="truncate text-base font-semibold" :title="filename">{{ filename }}</h3>
          <button type="button" class="icon-btn size-7 shrink-0" :disabled="!!downloading" title="下载脱敏副本" :aria-label="`下载 ${filename} 的脱敏副本`" @click="download(fileId, filename)">
            <LoaderCircle v-if="downloading === fileId" class="size-4 animate-spin motion-reduce:animate-none" />
            <Download v-else class="size-4" />
          </button>
        </div>
        <p class="mt-0.5 text-xs leading-5 text-faint">AI 看到的样子：被保护的列显示为令牌，真实内容留在本机</p>
      </div>
    </template>

    <div v-if="!preview && loading" class="flex justify-center py-12" role="status" aria-label="加载中">
      <LoaderCircle class="size-5 animate-spin text-mute motion-reduce:animate-none" />
    </div>

    <div v-else-if="!preview" class="flex flex-col items-center gap-3 py-10 text-center">
      <p class="max-w-md break-words text-sm leading-6 text-error" role="alert">{{ error }}</p>
      <button type="button" class="btn-line !px-3 !py-1.5 text-xs" @click="load(last.sheet, last.offset)"><RotateCw class="size-3.5" />重试</button>
    </div>

    <div v-else class="space-y-3">
      <div v-if="preview.sheets.length > 1" class="flex flex-wrap gap-1.5" role="group" aria-label="工作表">
        <button
          v-for="item in preview.sheets"
          :key="item.sheet"
          type="button"
          class="max-w-[14rem] truncate rounded-full border px-3 py-1 text-xs transition disabled:opacity-60"
          :class="item.sheet === preview.sheet ? 'border-ink bg-ink text-white' : 'border-line text-mute hover:border-ink/30'"
          :title="`${item.sheet} · ${formatCount(item.rows)} 行`"
          :aria-pressed="item.sheet === preview.sheet"
          :disabled="loading"
          @click="pickSheet(item.sheet)"
        >{{ item.sheet }}</button>
      </div>

      <p v-if="error" class="flex flex-wrap items-center gap-2 break-words rounded-xl bg-error/5 px-3 py-2 text-xs leading-5 text-error" role="alert">
        <span class="min-w-0 flex-1">{{ error }}</span>
        <button type="button" class="font-medium underline" @click="load(last.sheet, last.offset)">重试</button>
      </p>
      <p v-else-if="!protectedCount" class="text-xs leading-5 text-faint">这张工作表里没有被保护的列</p>

      <div class="relative">
        <div class="overflow-auto rounded-xl border border-line bg-white transition-opacity" :class="loading ? 'opacity-50' : ''" style="max-height: 52vh" data-testid="privacy-copy-table">
          <table class="w-full border-separate border-spacing-0 text-left text-[12px]">
            <thead>
              <tr>
                <th class="sticky top-0 z-10 w-10 border-b border-line bg-paper px-2 py-1.5 text-right align-bottom font-normal text-faint">#</th>
                <th
                  v-for="(column, index) in preview.columns"
                  :key="index"
                  class="sticky top-0 z-10 border-b border-line px-3 py-1.5 text-left align-bottom font-medium text-ink"
                  :class="column.protected ? 'bg-[#efeffc]' : 'bg-paper'"
                >
                  <div class="flex flex-col items-start gap-0.5">
                    <span class="whitespace-nowrap">{{ column.name }}</span>
                    <span v-if="column.protected" class="tag !bg-white !text-[#5558c8]" :title="column.action_label ?? undefined">{{ column.kind_label }}</span>
                  </div>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, r) in preview.rows" :key="r" class="hover:bg-paper/60">
                <td class="border-b border-line/70 px-2 py-1 text-right font-mono text-[11px] text-faint">{{ preview.offset + r + 1 }}</td>
                <td
                  v-for="(value, c) in row"
                  :key="c"
                  class="max-w-[260px] truncate border-b border-line/70 px-3 py-1"
                  :class="[preview.columns[c]?.protected ? 'bg-[#f8f8fe] font-mono text-[11px]' : '', value === null ? 'text-faint' : '']"
                  :title="cell(value)"
                >{{ cell(value) }}</td>
              </tr>
              <tr v-if="!preview.rows.length">
                <td :colspan="preview.columns.length + 1" class="px-3 py-6 text-center text-faint">没有数据</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="loading" class="pointer-events-none absolute inset-0 flex items-center justify-center" role="status" aria-label="加载中">
          <LoaderCircle class="size-5 animate-spin text-mute motion-reduce:animate-none" />
        </div>
      </div>
    </div>

    <template v-if="preview" #footer>
      <div class="flex w-full items-center justify-between gap-3">
        <span class="min-w-0 text-xs tabular-nums text-faint" aria-live="polite">{{ range }}</span>
        <div class="flex shrink-0 items-center gap-1">
          <button type="button" class="icon-btn" :disabled="!hasPrev || loading" title="上一页" aria-label="上一页" @click="page(-1)"><ChevronLeft class="size-4" /></button>
          <button type="button" class="icon-btn" :disabled="!hasNext || loading" title="下一页" aria-label="下一页" @click="page(1)"><ChevronRight class="size-4" /></button>
        </div>
      </div>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
/** Unified original-file preview. XLSX/XLSM/CSV are parsed entirely in a Worker. */
import { ChevronDown, ChevronLeft, ChevronRight, CircleAlert, Download, FileSpreadsheet, LoaderCircle, Maximize2, Minimize2, RotateCw, X } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { fileApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo } from '@/api/types'
import DataTable from '@/components/common/DataTable.vue'

import { parseInWorker } from './worker-client'

const props = defineProps<{ file?: FileInfo; localFile?: File }>()
const emit = defineEmits<{ close: [] }>()
const panel = ref<HTMLElement>()
const host = ref<HTMLElement>()
const phase = ref<'download' | 'parse' | 'render' | 'ready'>('download')
const error = ref('')
const warnings = ref<string[]>([])
const showWarnings = ref(false)
const expanded = ref(false)
const filename = computed(() => props.localFile?.name ?? props.file?.filename ?? '表格预览')
const size = computed(() => props.localFile?.size ?? props.file?.size ?? 0)
const legacy = computed(() => /\.xls$/i.test(filename.value))
const legacySheet = ref('')
const legacySheets = ref<string[]>([])
const legacyPage = ref<{ columns: string[]; rows: unknown[][]; total: number } | null>(null)
const legacyOffset = ref(0)
const legacyBusy = ref(false)
const stats = ref<{ sheetCount: number; cellCount: number } | null>(null)
const busyText = computed(() => ({ download: '正在读取文件…', parse: '正在解析表格和样式…', render: '正在打开表格…', ready: '' })[phase.value])
let controller: AbortController | null = null
let viewer: { dispose(): void } | null = null
let previousFocus: HTMLElement | null = null
let previousOverflow = ''
let loadId = 0

async function loadLegacy(offset = 0) {
  const file = props.file
  const localFile = props.localFile
  if (!file && !localFile) return
  const id = loadId
  legacyBusy.value = true
  error.value = ''
  try {
    const page = localFile
      ? await fileApi.previewUpload(localFile, legacySheet.value || undefined, offset, 100, controller?.signal)
      : await fileApi.preview(file!.file_id, legacySheet.value || undefined, offset, 100)
    if (id !== loadId) return
    legacyPage.value = page
    legacySheets.value = page.sheets
    legacySheet.value = page.sheet
    legacyOffset.value = offset
    phase.value = 'ready'
  } catch (err) {
    if (id === loadId) error.value = errorText(err)
  } finally {
    if (id === loadId) legacyBusy.value = false
  }
}

async function load() {
  const id = ++loadId
  controller?.abort()
  controller = new AbortController()
  const signal = controller.signal
  viewer?.dispose()
  viewer = null
  host.value?.replaceChildren()
  error.value = ''
  warnings.value = []
  showWarnings.value = false
  stats.value = null
  legacyPage.value = null
  legacySheets.value = []
  legacySheet.value = ''
  legacyBusy.value = false
  phase.value = 'download'
  if (legacy.value) {
    warnings.value = [props.localFile
      ? '旧版 .xls 使用临时数据预览，不保留原始排版；文件不会保存到会话。'
      : '旧版 .xls 当前提供数据预览；查看原始排版请下载文件，或另存为 .xlsx 后上传。']
    await loadLegacy()
    return
  }
  try {
    if (!props.localFile && !props.file) throw new Error('没有可预览的文件。')
    if (!/\.(xlsx|xlsm|csv)$/i.test(filename.value)) throw new Error('此文件暂不支持在线预览，请使用 Excel 查看。')
    if (size.value > 25 * 1024 * 1024) throw new Error('文件超过 25 MB，请使用 Excel 查看，或拆分文件再添加。')
    const buffer = props.localFile
      ? await props.localFile.arrayBuffer()
      : await fileApi.content(props.file!.file_id, signal)
    if (signal.aborted) return
    if (buffer.byteLength > 25 * 1024 * 1024) throw new Error('文件超过 25 MB，请下载后查看。')
    phase.value = 'parse'
    const parsed = await parseInWorker(buffer, filename.value, signal)
    if (signal.aborted) return
    warnings.value = parsed.warnings
    stats.value = parsed.stats
    phase.value = 'render'
    await nextTick()
    const { mountWorkbook } = await import('./viewer')
    if (signal.aborted || !host.value) return
    const mounted = await mountWorkbook(host.value, parsed.workbook)
    if (signal.aborted) mounted.dispose()
    else {
      viewer = mounted
      phase.value = 'ready'
    }
  } catch (err) {
    if (!signal.aborted && id === loadId) error.value = errorText(err)
  }
}

function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopImmediatePropagation()
    emit('close')
  }
  if (event.key === 'Tab' && panel.value) {
    const elements = [...panel.value.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, [tabindex="0"]')]
      .filter((element) => !element.hasAttribute('disabled') && element.getClientRects().length > 0)
    const first = elements[0]
    const last = elements.at(-1)
    if (event.shiftKey && (document.activeElement === first || !panel.value.contains(document.activeElement))) {
      event.preventDefault()
      last?.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first?.focus()
    }
  }
}

watch([() => props.file?.file_id, () => props.localFile], () => load())
onMounted(() => {
  previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', onKey, true)
  panel.value?.focus()
  void load()
})
onBeforeUnmount(() => {
  ++loadId
  controller?.abort()
  viewer?.dispose()
  window.removeEventListener('keydown', onKey, true)
  document.body.style.overflow = previousOverflow
  if (previousFocus?.isConnected) previousFocus.focus()
})
</script>

<template>
  <Teleport to="body">
    <div class="fixed inset-0 z-[6000] flex items-center justify-center bg-black/35 backdrop-blur-sm" @click.self="emit('close')">
      <section ref="panel" role="dialog" aria-modal="true" aria-labelledby="workbook-preview-title" tabindex="-1" class="workbook-preview flex min-h-0 flex-col overflow-hidden border border-line bg-white shadow-2xl outline-none" :class="expanded ? 'h-[100dvh] w-full' : 'h-[90dvh] w-[96vw] max-w-[1600px] rounded-2xl'">
        <header class="flex shrink-0 items-center gap-3 border-b border-line px-4 py-3 sm:px-5">
          <span class="rounded-xl bg-fun-green/10 p-2 text-fun-green"><FileSpreadsheet class="size-5" /></span>
          <div class="min-w-0 flex-1">
            <h2 id="workbook-preview-title" class="truncate text-sm font-semibold" :title="filename">{{ filename }}</h2>
            <p class="mt-0.5 text-xs text-mute">只读预览<span v-if="stats"> · {{ stats.sheetCount }} 个工作表</span></p>
          </div>
          <a v-if="file && !localFile" :href="file.download_url || fileApi.downloadUrl(file.file_id)" class="btn-line shrink-0 py-1.5 text-xs" title="下载原始文件"><Download class="size-4" /><span class="hidden sm:inline">下载</span></a>
          <button type="button" class="icon-btn" :aria-label="expanded ? '还原窗口' : '放大窗口'" :title="expanded ? '还原窗口' : '放大窗口'" @click="expanded = !expanded"><Minimize2 v-if="expanded" class="size-4" /><Maximize2 v-else class="size-4" /></button>
          <button type="button" class="icon-btn" aria-label="关闭文件预览" title="关闭（Esc）" @click="emit('close')"><X class="size-5" /></button>
        </header>

        <div v-if="warnings.length" class="shrink-0 border-b border-amber-100 bg-amber-50 px-4 py-2 text-xs text-amber-800">
          <button type="button" class="flex w-full items-center gap-2 text-left" :aria-expanded="showWarnings" @click="showWarnings = !showWarnings">
            <CircleAlert class="size-3.5 shrink-0" /><span class="flex-1">{{ warnings.length === 1 ? warnings[0] : `有 ${warnings.length} 项显示提示，点击查看` }}</span><ChevronDown v-if="warnings.length > 1" class="size-3.5" :class="{ 'rotate-180': showWarnings }" />
          </button>
          <ul v-if="showWarnings && warnings.length > 1" class="mt-2 list-disc space-y-1 pl-5"><li v-for="warning in warnings" :key="warning">{{ warning }}</li></ul>
        </div>

        <div v-if="error" class="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
          <CircleAlert class="size-8 text-mute" /><p role="alert" class="max-w-lg text-sm leading-6 text-mute">{{ error }}</p>
          <button type="button" class="btn-line" @click="load"><RotateCw class="size-4" />重新加载</button>
        </div>
        <template v-else-if="legacy && legacyPage">
          <div class="flex shrink-0 items-center gap-3 border-b border-line px-4 py-2 text-xs text-mute">
            <label for="legacy-sheet">工作表</label><select id="legacy-sheet" v-model="legacySheet" class="field w-auto py-1" :disabled="legacyBusy" @change="loadLegacy()"><option v-for="name in legacySheets" :key="name" :value="name">{{ name }}</option></select>
            <span>{{ legacyPage.total }} 行 × {{ legacyPage.columns.length }} 列</span>
          </div>
          <div class="min-h-0 flex-1 overflow-auto px-4 py-3"><DataTable :columns="legacyPage.columns" :rows="legacyPage.rows" :offset="legacyOffset" max-height="100%" /></div>
          <footer class="flex shrink-0 items-center justify-between border-t border-line px-4 py-2 text-xs text-mute">
            <span>{{ legacyPage.total ? `${legacyOffset + 1}–${Math.min(legacyOffset + 100, legacyPage.total)}` : '0' }} / {{ legacyPage.total }} 行</span>
            <div class="flex gap-2"><button type="button" class="btn-line py-1" :disabled="!legacyOffset || legacyBusy" @click="loadLegacy(legacyOffset - 100)"><ChevronLeft class="size-4" />上一页</button><button type="button" class="btn-line py-1" :disabled="legacyOffset + 100 >= legacyPage.total || legacyBusy" @click="loadLegacy(legacyOffset + 100)">下一页<ChevronRight class="size-4" /></button></div>
          </footer>
        </template>
        <div v-else class="relative min-h-0 flex-1">
          <div ref="host" class="absolute inset-0" data-testid="workbook-canvas" />
          <div v-if="phase !== 'ready'" class="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-white" role="status"><LoaderCircle class="size-7 animate-spin text-mute" /><span class="text-sm text-mute">{{ busyText }}</span></div>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.workbook-preview :deep(.univer-workbench) { height: 100%; }
</style>

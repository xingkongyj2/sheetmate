<script setup lang="ts">
/** Unified original-file preview. Images bypass the workbook parser. */
import { ChevronDown, ChevronLeft, ChevronRight, CircleAlert, Download, Sheet, Image, LoaderCircle, Maximize2, Minimize2, RotateCw, X } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { fileApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo } from '@/api/types'
import DataTable from '@/components/common/DataTable.vue'
import { isImageFile } from '@/composables/attachmentFiles'

import type { SheetMeta } from './excel'
import type { WorkbookViewer } from './viewer'
import { openInWorker, type PreviewSession } from './worker-client'

// page：作为独立页面整页展示（新标签页里的预览页）；否则是盖在当前页面上的弹层
const props = defineProps<{ file?: FileInfo; localFile?: File; page?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const panel = ref<HTMLElement>()
const host = ref<HTMLElement>()
const phase = ref<'download' | 'parse' | 'render' | 'ready'>('download')
// 按表加载：先只拿到工作表目录，看哪张才解析、渲染哪张
const sheets = ref<SheetMeta[]>([])
const selectedSheet = ref(-1)
const sheetBusy = ref(false)
const sheetError = ref('')
const tabColors = ref<Record<number, string>>({})
const tabs = computed(() => {
  const visible = sheets.value.filter((sheet) => sheet.hidden === 0)
  return visible.length ? visible : sheets.value
})
const error = ref('')
const warnings = ref<string[]>([])
const showWarnings = ref(false)
const expanded = ref(false)
const filename = computed(() => props.localFile?.name ?? props.file?.filename ?? '表格预览')
const size = computed(() => props.localFile?.size ?? props.file?.size ?? 0)
const legacy = computed(() => /\.xls$/i.test(filename.value))
const image = computed(() => isImageFile(filename.value, props.localFile?.type ?? props.file?.media_type))
const dataMode = ref(false)
const supportsDataPreview = computed(() => !image.value && /\.(xlsx|xlsm|xls|csv)$/i.test(filename.value))
const imageSrc = ref('')
let localImageUrl = ''
const legacySheet = ref('')
const legacySheets = ref<string[]>([])
const legacyPage = ref<{ columns: string[]; rows: unknown[][]; total: number; has_more?: boolean; total_exact?: boolean } | null>(null)
const legacyOffset = ref(0)
const legacyBusy = ref(false)
const busyText = computed(() => dataMode.value ? '正在读取当前页数据…' : ({ download: '正在读取文件…', parse: '正在读取工作表…', render: '正在打开表格…', ready: '' })[phase.value])
const hasNextPage = computed(() => legacyPage.value?.has_more ?? (legacyOffset.value + 100 < (legacyPage.value?.total ?? 0)))
let controller: AbortController | null = null
let viewer: WorkbookViewer | null = null
let session: PreviewSession | null = null
let sheetRequest = 0
let baseWarnings: string[] = []
const sheetWarnings = new Map<number, string[]>()
let previousFocus: HTMLElement | null = null
let previousOverflow = ''
let loadId = 0
let pageRequestId = 0

function dataPreviewWarnings(): string[] {
  if (legacy.value) return [props.localFile
    ? '旧版 .xls 使用临时数据预览，不保留原始排版；文件不会保存到会话。'
    : '旧版 .xls 当前提供数据预览；查看原始排版请下载文件，或另存为 .xlsx 后上传。']
  return ['当前使用分页数据预览，保留原始行列位置；样式、合并效果和图表请下载原文件查看。',
    ...(props.localFile ? ['此文件由本应用服务临时读取，不会保存到会话。'] : [])]
}

async function loadLegacy(offset = 0) {
  const file = props.file
  const localFile = props.localFile
  if (!file && !localFile) return
  const id = loadId
  const requestId = ++pageRequestId
  const signal = controller?.signal
  legacyBusy.value = true
  error.value = ''
  try {
    const page = legacy.value
      ? localFile
        ? await fileApi.previewUpload(localFile, legacySheet.value || undefined, offset, 100, signal)
        : await fileApi.preview(file!.file_id, legacySheet.value || undefined, offset, 100)
      : localFile
        ? await fileApi.previewDataUpload(localFile, legacySheet.value || undefined, offset, 100, signal)
        : await fileApi.previewData(file!.file_id, legacySheet.value || undefined, offset, 100, signal)
    if (id !== loadId || requestId !== pageRequestId || signal?.aborted) return
    legacyPage.value = page
    legacySheets.value = page.sheets
    legacySheet.value = page.sheet
    legacyOffset.value = offset
    const pageWarnings = 'warnings' in page && Array.isArray(page.warnings) ? page.warnings : []
    warnings.value = [...new Set([...dataPreviewWarnings(), ...pageWarnings])]
    phase.value = 'ready'
  } catch (err) {
    if (id === loadId && requestId === pageRequestId && !signal?.aborted) error.value = errorText(err)
  } finally {
    if (id === loadId && requestId === pageRequestId) legacyBusy.value = false
  }
}

async function load(preferData = false) {
  const id = ++loadId
  controller?.abort()
  controller = new AbortController()
  const signal = controller.signal
  viewer?.dispose()
  viewer = null
  session?.dispose()
  session = null
  sheets.value = []
  selectedSheet.value = -1
  sheetBusy.value = false
  sheetError.value = ''
  tabColors.value = {}
  sheetWarnings.clear()
  baseWarnings = []
  ++sheetRequest
  if (localImageUrl) URL.revokeObjectURL(localImageUrl)
  localImageUrl = ''
  imageSrc.value = ''
  host.value?.replaceChildren()
  error.value = ''
  warnings.value = []
  showWarnings.value = false
  legacyPage.value = null
  legacySheets.value = []
  legacySheet.value = ''
  legacyBusy.value = false
  dataMode.value = !image.value && (legacy.value || preferData)
  phase.value = 'download'
  if (image.value) {
    if (props.localFile) {
      localImageUrl = URL.createObjectURL(props.localFile)
      imageSrc.value = localImageUrl
    } else if (props.file) imageSrc.value = props.file.preview_url || `/api/files/${encodeURIComponent(props.file.file_id)}/image`
    return
  }
  if (dataMode.value || (supportsDataPreview.value && size.value > 25 * 1024 * 1024)) {
    dataMode.value = true
    await loadLegacy()
    return
  }
  let canFallBack = false
  try {
    if (!props.localFile && !props.file) throw new Error('没有可预览的文件。')
    // 表格查看器的代码包较大：和下载、解析文件同时加载，不用等它们做完才开始
    const viewerModule = import('./viewer')
    viewerModule.catch(() => undefined)
    if (!/\.(xlsx|xlsm|csv)$/i.test(filename.value)) throw new Error('此文件暂不支持在线预览，请使用 Excel 查看。')
    const buffer = props.localFile
      ? await props.localFile.arrayBuffer()
      : await fileApi.content(props.file!.file_id, signal)
    if (signal.aborted || id !== loadId) return
    canFallBack = true
    if (buffer.byteLength > 25 * 1024 * 1024) throw new Error('文件超过 25 MB，请下载后查看。')
    phase.value = 'parse'
    const opened = await openInWorker(buffer, filename.value, signal)
    if (signal.aborted || id !== loadId) { opened.dispose(); return }
    session = opened
    sheets.value = opened.sheets
    baseWarnings = opened.warnings
    warnings.value = [...baseWarnings]
    phase.value = 'render'
    await nextTick()
    const { createViewer } = await viewerModule
    if (signal.aborted || id !== loadId || !host.value) return
    viewer = createViewer(host.value)
    // 第一张表读不出来（比如超出完整样式的范围）就整体退到分页预览；其余表单独提示，不影响别的表
    await openSheet(opened.active, true)
    if (signal.aborted || id !== loadId) return
    phase.value = 'ready'
  } catch (err) {
    if (signal.aborted || id !== loadId) return
    if (canFallBack && supportsDataPreview.value) {
      viewer?.dispose()
      viewer = null
      host.value?.replaceChildren()
      dataMode.value = true
      phase.value = 'parse'
      await loadLegacy()
      if (error.value && id === loadId) error.value = `原样式预览未能打开，分页数据预览也未能完成：${error.value}`
    } else error.value = errorText(err)
  }
}

/** 显示一张工作表：解析这一张、换掉当前显示的内容。连续点几个标签时只认最后一次。 */
async function openSheet(index: number, first = false) {
  const current = session
  const shown = viewer
  if (!current || !shown) return
  const request = ++sheetRequest
  const id = loadId
  selectedSheet.value = index
  sheetBusy.value = true
  sheetError.value = ''
  try {
    const parsed = await current.loadSheet(index)
    if (request !== sheetRequest || id !== loadId) return
    await shown.show(parsed.workbook)
    if (request !== sheetRequest || id !== loadId) return
    sheetWarnings.set(index, parsed.warnings)
    warnings.value = [...new Set([...baseWarnings, ...[...sheetWarnings.values()].flat()])]
    const color = Object.values(parsed.workbook.sheets)[0]?.tabColor
    if (color) tabColors.value = { ...tabColors.value, [index]: color }
  } catch (err) {
    if (request !== sheetRequest || id !== loadId) return
    if (first) throw err
    sheetError.value = errorText(err)
  } finally {
    if (request === sheetRequest) sheetBusy.value = false
  }
}

function onTabKey(event: KeyboardEvent, position: number) {
  const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
  const edge = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.value.length - 1 : null
  const next = edge ?? (step ? position + step : null)
  if (next === null || next < 0 || next >= tabs.value.length) return
  event.preventDefault()
  event.stopPropagation()
  const target = tabs.value[next]!
  void openSheet(target.index)
  nextTick(() => (event.currentTarget as HTMLElement | null)?.parentElement?.querySelectorAll<HTMLElement>('[role=tab]')[next]?.focus())
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
  if (!props.page) document.body.style.overflow = 'hidden'
  else document.title = filename.value
  window.addEventListener('keydown', onKey, true)
  panel.value?.focus()
  void load()
})
onBeforeUnmount(() => {
  ++loadId
  controller?.abort()
  session?.dispose()
  viewer?.dispose()
  if (localImageUrl) URL.revokeObjectURL(localImageUrl)
  window.removeEventListener('keydown', onKey, true)
  if (!props.page) document.body.style.overflow = previousOverflow
  if (previousFocus?.isConnected) previousFocus.focus()
})
</script>

<template>
  <Teleport to="body" :disabled="page">
    <div :class="page ? 'h-dvh w-full' : 'fixed inset-0 z-[6000] flex items-center justify-center bg-black/35 backdrop-blur-sm'" @click.self="!page && emit('close')">
      <section ref="panel" :role="page ? 'main' : 'dialog'" :aria-modal="page ? undefined : 'true'" aria-labelledby="workbook-preview-title" tabindex="-1" class="workbook-preview flex min-h-0 flex-col overflow-hidden bg-white outline-none" :class="page ? 'h-dvh w-full' : ['border border-line shadow-2xl', expanded ? 'h-[100dvh] w-full' : 'h-[90dvh] w-[96vw] max-w-[1600px] rounded-2xl']">
        <header class="flex shrink-0 items-center gap-2.5 border-b border-line px-4 py-1.5 sm:px-5">
          <span class="rounded-lg bg-paper p-1.5 text-[#4a4743]"><Image v-if="image" class="size-4" /><Sheet v-else class="size-4" /></span>
          <div class="min-w-0 flex-1">
            <h2 id="workbook-preview-title" class="truncate text-sm font-semibold" :title="filename">{{ filename }}</h2>
          </div>
          <a v-if="file && !localFile" :href="file.download_url || fileApi.downloadUrl(file.file_id)" class="btn-line shrink-0 !h-8 py-0 text-xs" :class="{ 'order-last': page }" title="下载原始文件"><Download class="size-4" /><span class="hidden sm:inline">下载</span></a>
          <button v-if="supportsDataPreview && !legacy && (!dataMode || size <= 25 * 1024 * 1024)" type="button" class="btn-line shrink-0 !h-8 py-0 text-xs" @click="load(!dataMode)">{{ dataMode ? '原样式预览' : '分页预览' }}</button>
          <button v-if="!page" type="button" class="icon-btn" :aria-label="expanded ? '还原窗口' : '放大窗口'" :title="expanded ? '还原窗口' : '放大窗口'" @click="expanded = !expanded"><Minimize2 v-if="expanded" class="size-4" /><Maximize2 v-else class="size-4" /></button>
          <button v-if="!page" type="button" class="icon-btn" aria-label="关闭文件预览" :title="page ? '关闭此页（Esc）' : '关闭（Esc）'" @click="emit('close')"><X class="size-4" /></button>
        </header>

        <div v-if="warnings.length" class="shrink-0 border-b border-amber-100 bg-amber-50 px-4 py-2 text-xs text-amber-800">
          <button type="button" class="flex w-full items-center gap-2 text-left" :aria-expanded="showWarnings" @click="showWarnings = !showWarnings">
            <CircleAlert class="size-3.5 shrink-0" /><span class="flex-1">{{ warnings.length === 1 ? warnings[0] : `有 ${warnings.length} 项显示提示，点击查看` }}</span><ChevronDown v-if="warnings.length > 1" class="size-3.5" :class="{ 'rotate-180': showWarnings }" />
          </button>
          <ul v-if="showWarnings && warnings.length > 1" class="mt-2 list-disc space-y-1 pl-5"><li v-for="warning in warnings" :key="warning">{{ warning }}</li></ul>
        </div>

        <div v-if="error" class="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
          <CircleAlert class="size-8 text-mute" /><p role="alert" class="max-w-lg text-sm leading-6 text-mute">{{ error }}</p>
          <button type="button" class="btn-line" @click="load(dataMode)"><RotateCw class="size-4" />重新加载</button>
        </div>
        <div v-else-if="image" class="relative flex min-h-0 flex-1 items-center justify-center overflow-auto bg-paper p-4">
          <img v-if="imageSrc" :key="imageSrc" :src="imageSrc" :alt="filename" class="max-h-full max-w-full object-contain" data-testid="image-preview" @load="phase = 'ready'" @error="error = '图片加载失败，请重新加载或下载查看。'" />
          <div v-if="phase !== 'ready'" class="absolute inset-0 flex items-center justify-center gap-3 bg-paper" role="status"><LoaderCircle class="size-7 animate-spin text-mute" /><span class="text-sm text-mute">正在加载图片…</span></div>
        </div>
        <template v-else-if="dataMode && legacyPage">
          <div class="flex shrink-0 items-center gap-3 border-b border-line px-4 py-2 text-xs text-mute">
            <label for="legacy-sheet">工作表</label><select id="legacy-sheet" v-model="legacySheet" class="field w-auto py-1" :disabled="legacyBusy" @change="loadLegacy()"><option v-for="name in legacySheets" :key="name" :value="name">{{ name }}</option></select>
            <span>{{ legacyPage.total_exact === false ? '按页读取' : `${legacyPage.total} 行` }} × {{ legacyPage.columns.length }} 列</span><LoaderCircle v-if="legacyBusy" class="size-3.5 animate-spin" />
          </div>
          <div class="min-h-0 flex-1 overflow-auto px-4 py-3"><DataTable :columns="legacyPage.columns" :rows="legacyPage.rows" :offset="legacyOffset" max-height="100%" /></div>
          <footer class="flex shrink-0 items-center justify-between border-t border-line px-4 py-2 text-xs text-mute">
            <span>{{ legacyPage.rows.length ? `${legacyOffset + 1}–${legacyOffset + legacyPage.rows.length}` : '0' }}{{ legacyPage.total_exact === false ? ' 行 · 总行数按需确认' : ` / ${legacyPage.total} 行` }}</span>
            <div class="flex gap-2"><button type="button" class="btn-line py-1" :disabled="!legacyOffset || legacyBusy" @click="loadLegacy(legacyOffset - 100)"><ChevronLeft class="size-4" />上一页</button><button type="button" class="btn-line py-1" :disabled="!hasNextPage || legacyBusy" @click="loadLegacy(legacyOffset + 100)">下一页<ChevronRight class="size-4" /></button></div>
          </footer>
        </template>
        <div v-else class="flex min-h-0 flex-1 flex-col">
          <div class="relative min-h-0 flex-1">
            <div ref="host" class="absolute inset-0" data-testid="workbook-canvas" />
            <div v-if="phase !== 'ready'" class="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-white" role="status"><LoaderCircle class="size-7 animate-spin text-mute" /><span class="text-sm text-mute">{{ busyText }}</span></div>
            <div v-else-if="sheetBusy" class="absolute inset-0 z-10 flex items-center justify-center bg-white/70" role="status" aria-label="正在读取工作表"><LoaderCircle class="size-6 animate-spin text-mute" /></div>
            <div v-if="sheetError && phase === 'ready'" class="absolute inset-0 z-20 flex flex-col items-center justify-center gap-4 bg-white px-6 text-center">
              <CircleAlert class="size-8 text-mute" /><p role="alert" class="max-w-lg text-sm leading-6 text-mute">{{ sheetError }}</p>
              <div class="flex gap-2"><button type="button" class="btn-line" @click="openSheet(selectedSheet)"><RotateCw class="size-4" />重试</button><button v-if="supportsDataPreview" type="button" class="btn-line" @click="load(true)">改用分页预览</button></div>
            </div>
          </div>
          <nav v-if="tabs.length" class="sheet-tabs flex shrink-0 items-stretch gap-0.5 overflow-x-auto border-t border-line bg-paper px-2" role="tablist" aria-label="工作表">
            <button v-for="(sheet, position) in tabs" :key="sheet.index" type="button" role="tab" :aria-selected="selectedSheet === sheet.index" :tabindex="selectedSheet === sheet.index ? 0 : -1" :title="sheet.name"
              class="sheet-tab" :class="{ 'is-on': selectedSheet === sheet.index }" @click="openSheet(sheet.index)" @keydown="onTabKey($event, position)">
              <span class="sheet-dot" :style="tabColors[sheet.index] ? { background: tabColors[sheet.index] } : undefined" aria-hidden="true" /><span class="truncate">{{ sheet.name }}</span>
            </button>
          </nav>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.workbook-preview :deep(.univer-workbench) { height: 100%; }
.sheet-tab { display: inline-flex; max-width: 14rem; flex: none; align-items: center; gap: 6px; border-radius: 0 0 8px 8px; padding: 7px 14px; color: #6b6b7a; font-size: 12px; white-space: nowrap; transition: background-color .15s, color .15s; }
.sheet-tab:hover { color: #14141c; }
.sheet-tab.is-on { background: #fff; color: #14141c; font-weight: 600; box-shadow: inset 0 -2px 0 #14141c; }
.sheet-tab:focus-visible { outline: 2px solid #14141c; outline-offset: -2px; }
.sheet-dot { display: none; width: 8px; height: 8px; flex: none; border-radius: 9999px; }
.sheet-dot[style] { display: inline-block; }
</style>

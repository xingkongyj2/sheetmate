<script setup lang="ts">
/**
 * 极简双栏表格比对页：
 * 左侧为改前（基准），右侧输入另一个表格链接或选择本地文件载入改后（目标）。
 * 一切采用默认配置（保留原样式、智能对齐、同步滚动与选区），直接清晰展示差异。
 */
import {
  ArrowLeftRight,
  ArrowRight,
  Check,
  CircleAlert,
  Columns2,
  FileSpreadsheet,
  GitCompareArrows,
  Link2,
  LoaderCircle,
  Minus,
  Plus,
  RotateCw,
  Sheet,
  Upload,
  X,
} from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { fileApi } from '@/api'
import { errorText } from '@/api/http'
import { isImageFile } from '@/composables/attachmentFiles'

import type { SheetMeta } from '../workbook-preview/excel'
import type { CellPos, CompareViewer, ScrollSyncState } from './compare-viewer'
import { buildSheetPairs } from './diff'
import type { CellDiffEntry, CompareSource, DiffOptions, SheetDiffResult, SheetDiffStats, SheetPair } from './types'
import { isSameCompareSource, parseCompareTargetInput } from './url'
import { createCompareWorker, type CompareSide, type CompareWorkerClient } from './worker-client'

const DEFAULT_DIFF_OPTIONS: DiffOptions = {
  alignMode: 'smart',
  onlyDiffRows: false,
  showFormulas: false,
  preserveStyles: true,
}

const props = defineProps<{
  initialLeft: CompareSource | null
  initialRight: CompareSource | null
}>()

const emit = defineEmits<{
  'update-sources': [payload: { left: CompareSource | null; right: CompareSource | null }]
}>()

interface SideFileMeta {
  name: string
  size: number
  source: CompareSource
  fileId?: string
}

// ---- 左右文件状态 ----
const leftMeta = ref<SideFileMeta | null>(null)
const rightMeta = ref<SideFileMeta | null>(null)
const leftSheets = ref<SheetMeta[]>([])
const rightSheets = ref<SheetMeta[]>([])
const leftActiveIndex = ref(0)
const rightActiveIndex = ref(0)

const leftLoading = ref(false)
const rightLoading = ref(false)
const leftError = ref('')
const rightError = ref('')

// ---- 右侧 URL 输入与更换 ----
const urlInput = ref('')
const urlInputError = ref('')
const editingRight = ref(false)
const fileInputRef = ref<HTMLInputElement>()
const leftFileInputRef = ref<HTMLInputElement>()

// ---- 比对结果 ----
const activePairKey = ref('')
const diffBusy = ref(false)
const diffError = ref('')
const diffResult = ref<SheetDiffResult | null>(null)
const pairSummaries = ref<Record<string, SheetDiffStats | 'loading'>>({})

// 单侧预览（右侧尚未选表时，左侧可正常切表查看）
const singleLeftSheet = ref(0)

// 选中差异格（仅在点中差异格时展示一行改前/改后对照）
const selectedDiff = ref<CellDiffEntry | null>(null)

// DOM 与 Univer 实例
const leftHost = ref<HTMLElement>()
const rightHost = ref<HTMLElement>()
let leftViewer: CompareViewer | null = null
let rightViewer: CompareViewer | null = null
let leftScrollUnsub: (() => void) | null = null
let rightScrollUnsub: (() => void) | null = null
let leftSelUnsub: (() => void) | null = null
let rightSelUnsub: (() => void) | null = null

let worker: CompareWorkerClient | null = null
let viewerModulePromise: Promise<typeof import('./compare-viewer')> | null = null
let leftAbort: AbortController | null = null
let rightAbort: AbortController | null = null
let diffRequestSeq = 0
let summaryEpoch = 0
let singleRequestSeq = 0

const hasBothFiles = computed(() => Boolean(leftMeta.value && rightMeta.value && leftSheets.value.length && rightSheets.value.length))

const sheetPairs = computed<SheetPair[]>(() => {
  if (!hasBothFiles.value) return []
  return buildSheetPairs(leftSheets.value, rightSheets.value)
})

const currentPair = computed<SheetPair | null>(() => {
  if (!sheetPairs.value.length) return null
  return sheetPairs.value.find((p) => p.key === activePairKey.value) ?? sheetPairs.value[0] ?? null
})

const leftVisibleTabs = computed(() => {
  const visible = leftSheets.value.filter((s) => s.hidden === 0)
  return visible.length ? visible : leftSheets.value
})

function ensureWorker(): CompareWorkerClient {
  if (!worker) worker = createCompareWorker()
  return worker
}

function loadViewerModule() {
  if (!viewerModulePromise) {
    viewerModulePromise = import('./compare-viewer')
    viewerModulePromise.catch(() => { viewerModulePromise = null })
  }
  return viewerModulePromise
}

async function ensureLeftViewer(): Promise<CompareViewer | null> {
  await nextTick()
  if (!leftHost.value) return null
  if (!leftViewer) {
    const { createCompareViewer } = await loadViewerModule()
    if (!leftHost.value) return null
    leftViewer = createCompareViewer(leftHost.value)
    leftScrollUnsub = leftViewer.onScroll((state: ScrollSyncState) => {
      if (hasBothFiles.value) rightViewer?.setScrollState(state)
    })
    leftSelUnsub = leftViewer.onSelection((pos: CellPos) => {
      if (!hasBothFiles.value) return
      rightViewer?.focusCell(pos.row, pos.col, false)
      handleSelectionChange(pos)
    })
  }
  return leftViewer
}

async function ensureRightViewer(): Promise<CompareViewer | null> {
  await nextTick()
  if (!rightHost.value) return null
  if (!rightViewer) {
    const { createCompareViewer } = await loadViewerModule()
    if (!rightHost.value) return null
    rightViewer = createCompareViewer(rightHost.value)
    rightScrollUnsub = rightViewer.onScroll((state: ScrollSyncState) => {
      if (hasBothFiles.value) leftViewer?.setScrollState(state)
    })
    rightSelUnsub = rightViewer.onSelection((pos: CellPos) => {
      if (!hasBothFiles.value) return
      leftViewer?.focusCell(pos.row, pos.col, false)
      handleSelectionChange(pos)
    })
  }
  return rightViewer
}

function disposeRightViewer() {
  rightScrollUnsub?.()
  rightSelUnsub?.()
  rightScrollUnsub = null
  rightSelUnsub = null
  rightViewer?.dispose()
  rightViewer = null
  rightHost.value?.replaceChildren()
}

function handleSelectionChange(pos: CellPos) {
  const key = `${pos.row}:${pos.col}`
  selectedDiff.value = diffResult.value?.cellDiffMap[key] ?? null
}

function validateFileName(filename: string, mediaType?: string) {
  if (isImageFile(filename, mediaType)) {
    throw new Error(`「${filename}」是图片文件，暂不支持表格比对。`)
  }
  if (/\.xls$/i.test(filename)) {
    throw new Error(`「${filename}」是旧版 .xls 格式，暂不支持在线表格比对，请另存为 .xlsx 后再比对。`)
  }
  if (!/\.(xlsx|xlsm|csv)$/i.test(filename)) {
    throw new Error(`「${filename}」不是支持的表格格式（支持 .xlsx、.xlsm、.csv）。`)
  }
}

async function fetchSourceBuffer(
  source: CompareSource,
  signal: AbortSignal,
): Promise<{ buffer: ArrayBuffer; name: string; size: number; fileId?: string }> {
  if (source.kind === 'file') {
    validateFileName(source.file.name, source.file.type)
    if (source.file.size > 25 * 1024 * 1024) throw new Error('文件超过 25 MB，暂不支持浏览器端直接比对。')
    const buffer = await source.file.arrayBuffer()
    return { buffer, name: source.file.name, size: source.file.size }
  }
  if (source.kind === 'local') {
    validateFileName(source.name)
    let blob: Blob
    try {
      blob = await (await fetch(source.blobUrl, { signal })).blob()
    } catch (err) {
      if (signal.aborted) throw err
      throw new Error('本地文件地址已失效，请回到原页面重新打开，或直接选择本地文件。')
    }
    if (blob.size > 25 * 1024 * 1024) throw new Error('文件超过 25 MB，暂不支持浏览器端直接比对。')
    const buffer = await blob.arrayBuffer()
    return { buffer, name: source.name, size: blob.size }
  }
  const info = await fileApi.get(source.fileId)
  if (signal.aborted) throw new DOMException('已取消加载', 'AbortError')
  validateFileName(info.filename, info.media_type)
  if (info.size > 25 * 1024 * 1024) throw new Error('文件超过 25 MB，暂不支持浏览器端直接比对。')
  const buffer = await fileApi.content(info.file_id, signal)
  if (buffer.byteLength > 25 * 1024 * 1024) throw new Error('文件超过 25 MB，暂不支持浏览器端直接比对。')
  return { buffer, name: info.filename, size: info.size, fileId: info.file_id }
}

async function loadSide(side: CompareSide, source: CompareSource, syncUrl = true) {
  const isLeft = side === 'left'
  if (isLeft) {
    leftAbort?.abort()
    leftAbort = new AbortController()
    leftLoading.value = true
    leftError.value = ''
  } else {
    rightAbort?.abort()
    rightAbort = new AbortController()
    rightLoading.value = true
    rightError.value = ''
    urlInputError.value = ''
  }
  const signal = (isLeft ? leftAbort : rightAbort)!.signal

  try {
    void loadViewerModule()
    const payload = await fetchSourceBuffer(source, signal)
    if (signal.aborted) return

    const opened = await ensureWorker().open(side, payload.buffer, payload.name)
    if (signal.aborted) return

    const meta: SideFileMeta = {
      name: payload.name,
      size: payload.size,
      source,
      fileId: payload.fileId,
    }

    if (isLeft) {
      leftMeta.value = meta
      leftSheets.value = opened.sheets
      leftActiveIndex.value = opened.active
      singleLeftSheet.value = opened.active
    } else {
      rightMeta.value = meta
      rightSheets.value = opened.sheets
      rightActiveIndex.value = opened.active
      editingRight.value = false
    }

    pairSummaries.value = {}
    diffResult.value = null
    selectedDiff.value = null

    if (syncUrl) {
      emit('update-sources', {
        left: leftMeta.value?.source ?? null,
        right: rightMeta.value?.source ?? null,
      })
    }

    updateDocumentTitle()

    if (hasBothFiles.value) {
      const pairs = sheetPairs.value
      const preferred = pairs.find((p) => p.leftIndex === leftActiveIndex.value)
        ?? pairs.find((p) => p.kind === 'matched')
        ?? pairs[0]
      activePairKey.value = preferred?.key ?? ''
      await runDiffForCurrentSheet()
      void computeAllSheetSummaries()
    } else if (isLeft && leftSheets.value.length) {
      await showSingleSheet('left', opened.active)
    } else if (!isLeft && rightSheets.value.length) {
      await showSingleSheet('right', opened.active)
    }
  } catch (err) {
    if (signal.aborted) return
    const msg = errorText(err)
    if (isLeft) leftError.value = msg
    else rightError.value = msg
  } finally {
    if (!signal.aborted) {
      if (isLeft) leftLoading.value = false
      else rightLoading.value = false
    }
  }
}

async function showSingleSheet(side: CompareSide, index: number) {
  const req = ++singleRequestSeq
  if (side === 'left') {
    singleLeftSheet.value = index
    leftActiveIndex.value = index
  } else {
    rightActiveIndex.value = index
  }
  diffError.value = ''
  try {
    const parsed = await ensureWorker().loadSingle(side, index)
    if (req !== singleRequestSeq || hasBothFiles.value) return
    const viewer = side === 'left' ? await ensureLeftViewer() : await ensureRightViewer()
    if (req !== singleRequestSeq || hasBothFiles.value || !viewer) return
    await viewer.show(parsed.workbook)
  } catch (err) {
    if (req !== singleRequestSeq) return
    if (side === 'left') leftError.value = errorText(err)
    else rightError.value = errorText(err)
  }
}

async function runDiffForCurrentSheet() {
  const pair = currentPair.value
  if (!pair || !hasBothFiles.value) return
  const req = ++diffRequestSeq
  diffBusy.value = true
  diffError.value = ''
  selectedDiff.value = null

  try {
    const { result } = await ensureWorker().diffSheet(
      pair.leftIndex,
      pair.rightIndex,
      DEFAULT_DIFF_OPTIONS,
      pair.name,
    )
    if (req !== diffRequestSeq) return

    diffResult.value = result
    pairSummaries.value = { ...pairSummaries.value, [pair.key]: result.stats }

    const [lv, rv] = await Promise.all([ensureLeftViewer(), ensureRightViewer()])
    if (req !== diffRequestSeq) return

    await Promise.all([
      lv?.show(result.leftWorkbook),
      rv?.show(result.rightWorkbook),
    ])
  } catch (err) {
    if (req !== diffRequestSeq) return
    diffError.value = errorText(err)
  } finally {
    if (req === diffRequestSeq) diffBusy.value = false
  }
}

async function computeAllSheetSummaries() {
  if (!hasBothFiles.value) return
  const epoch = ++summaryEpoch
  const pairs = [...sheetPairs.value]

  for (const pair of pairs) {
    if (epoch !== summaryEpoch) return
    if (pair.key === activePairKey.value && diffResult.value) {
      pairSummaries.value = { ...pairSummaries.value, [pair.key]: diffResult.value.stats }
      continue
    }
    if (pairSummaries.value[pair.key] && pairSummaries.value[pair.key] !== 'loading') continue
    pairSummaries.value = { ...pairSummaries.value, [pair.key]: 'loading' }
    try {
      const stats = await ensureWorker().diffSummary(pair.leftIndex, pair.rightIndex, DEFAULT_DIFF_OPTIONS.alignMode)
      if (epoch !== summaryEpoch) return
      pairSummaries.value = { ...pairSummaries.value, [pair.key]: stats }
    } catch {
      if (epoch !== summaryEpoch) return
      const next = { ...pairSummaries.value }
      delete next[pair.key]
      pairSummaries.value = next
    }
  }
}

function selectPair(key: string) {
  if (key === activePairKey.value) return
  activePairKey.value = key
  void runDiffForCurrentSheet()
}

async function submitRightUrl() {
  urlInputError.value = ''
  const parsed = parseCompareTargetInput(urlInput.value)
  if (!parsed.ok) {
    urlInputError.value = parsed.error
    return
  }
  if (leftMeta.value && isSameCompareSource(leftMeta.value.source, parsed.source)) {
    urlInputError.value = '输入的链接与左侧是同一个文件，请输入另一个表格的链接。'
    return
  }
  await loadSide('right', parsed.source, true)
}

function onPickLocalFile(side: CompareSide, event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  void loadSide(side, { kind: 'file', file }, true)
}

async function swapSides() {
  if (!leftMeta.value || !rightMeta.value || leftLoading.value || rightLoading.value || diffBusy.value) return
  await ensureWorker().swap()

  const prevLeftMeta = leftMeta.value
  const prevLeftSheets = leftSheets.value
  const prevLeftActive = leftActiveIndex.value

  leftMeta.value = rightMeta.value
  leftSheets.value = rightSheets.value
  leftActiveIndex.value = rightActiveIndex.value

  rightMeta.value = prevLeftMeta
  rightSheets.value = prevLeftSheets
  rightActiveIndex.value = prevLeftActive

  pairSummaries.value = {}
  selectedDiff.value = null

  const current = currentPair.value
  if (current) {
    const flipped = sheetPairs.value.find((p) => p.leftIndex === current.rightIndex && p.rightIndex === current.leftIndex)
    activePairKey.value = flipped?.key ?? sheetPairs.value[0]?.key ?? ''
  } else {
    activePairKey.value = sheetPairs.value[0]?.key ?? ''
  }

  emit('update-sources', {
    left: leftMeta.value?.source ?? null,
    right: rightMeta.value?.source ?? null,
  })
  updateDocumentTitle()
  await runDiffForCurrentSheet()
  void computeAllSheetSummaries()
}

function openChangeRight() {
  urlInput.value = ''
  urlInputError.value = ''
  editingRight.value = true
}

function updateDocumentTitle() {
  if (leftMeta.value && rightMeta.value) {
    document.title = `比对：${leftMeta.value.name} vs ${rightMeta.value.name}`
  } else if (leftMeta.value) {
    document.title = `比对：${leftMeta.value.name}`
  } else {
    document.title = '表格比对'
  }
}

watch(() => props.initialLeft, (next) => {
  if (next && !isSameCompareSource(next, leftMeta.value?.source ?? null)) {
    void loadSide('left', next, false)
  }
})

watch(() => props.initialRight, (next) => {
  if (next && !isSameCompareSource(next, rightMeta.value?.source ?? null)) {
    void loadSide('right', next, false)
  }
})

onMounted(() => {
  document.title = '表格比对'
  if (props.initialLeft) void loadSide('left', props.initialLeft, false)
  if (props.initialRight) void loadSide('right', props.initialRight, false)
})

onBeforeUnmount(() => {
  summaryEpoch++
  diffRequestSeq++
  singleRequestSeq++
  leftAbort?.abort()
  rightAbort?.abort()
  leftScrollUnsub?.()
  leftSelUnsub?.()
  leftViewer?.dispose()
  disposeRightViewer()
  worker?.dispose()
})
</script>

<template>
  <main class="workbook-compare flex h-dvh w-full flex-col overflow-hidden bg-white">
    <!-- 顶部极简栏：只保留左右文件信息、对调与更换 -->
    <header class="flex shrink-0 items-center gap-2 border-b border-line bg-white px-3.5 py-2 sm:px-4">
      <span class="flex size-7 shrink-0 items-center justify-center rounded-lg bg-ink text-white">
        <GitCompareArrows class="size-4" />
      </span>

      <div class="flex min-w-0 items-center gap-1.5 rounded-lg border border-line bg-paper/60 px-2.5 py-1">
        <span class="shrink-0 rounded bg-white px-1.5 py-0.5 text-[10px] font-semibold text-mute">改前</span>
        <span v-if="leftMeta" class="max-w-[200px] truncate text-xs font-medium text-ink sm:max-w-[280px]" :title="leftMeta.name">{{ leftMeta.name }}</span>
        <span v-else-if="leftLoading" class="flex items-center gap-1 text-xs text-mute"><LoaderCircle class="size-3 animate-spin" />加载中…</span>
        <button v-else type="button" class="text-xs text-ink underline underline-offset-2" @click="leftFileInputRef?.click()">选择文件</button>
      </div>

      <button
        type="button"
        class="icon-btn size-7 shrink-0"
        :disabled="!hasBothFiles || diffBusy"
        title="对调改前与改后文件"
        aria-label="对调改前与改后文件"
        @click="swapSides"
      >
        <ArrowLeftRight class="size-3.5" />
      </button>

      <div class="flex min-w-0 items-center gap-1.5 rounded-lg border border-line bg-paper/60 px-2.5 py-1">
        <span class="shrink-0 rounded bg-ink px-1.5 py-0.5 text-[10px] font-semibold text-white">改后</span>
        <span v-if="rightMeta" class="max-w-[200px] truncate text-xs font-medium text-ink sm:max-w-[280px]" :title="rightMeta.name">{{ rightMeta.name }}</span>
        <span v-else-if="rightLoading" class="flex items-center gap-1 text-xs text-mute"><LoaderCircle class="size-3 animate-spin" />加载中…</span>
        <span v-else class="text-xs text-faint">待输入目标表格链接</span>
        <button
          v-if="rightMeta"
          type="button"
          class="ml-0.5 rounded px-1.5 py-0.5 text-[11px] text-mute transition hover:bg-white hover:text-ink"
          title="更换要对比的表格"
          @click="openChangeRight"
        >
          更换
        </button>
      </div>
    </header>

    <!-- 更换右侧表格浮条 -->
    <div v-if="editingRight && rightMeta" class="flex shrink-0 flex-wrap items-center gap-2 border-b border-line bg-paper/70 px-4 py-2">
      <Link2 class="size-4 shrink-0 text-mute" />
      <form class="flex min-w-0 flex-1 items-center gap-2" @submit.prevent="submitRightUrl">
        <input
          v-model="urlInput"
          type="text"
          placeholder="粘贴新的表格浏览页链接（/preview/...）或文件 ID"
          class="h-8 min-w-[220px] flex-1 rounded-lg border border-line bg-white px-3 text-xs text-ink outline-none focus:border-ink/40"
          autofocus
        >
        <button type="submit" class="btn-ink !h-8 !px-4 !py-0 text-xs" :disabled="rightLoading">载入比对</button>
        <button type="button" class="btn-line !h-8 !px-3 !py-0 text-xs" @click="fileInputRef?.click()">
          <Upload class="size-3.5" />本地文件
        </button>
        <button type="button" class="icon-btn size-7" aria-label="取消更换" @click="editingRight = false; urlInputError = ''">
          <X class="size-3.5" />
        </button>
      </form>
      <p v-if="urlInputError || rightError" class="w-full text-xs text-error">{{ urlInputError || rightError }}</p>
    </div>

    <!-- 点中差异单元格时，轻展示一行改前/改后值与公式对照 -->
    <div v-if="hasBothFiles && selectedDiff" class="flex shrink-0 flex-wrap items-center gap-2 border-b border-line bg-paper/45 px-4 py-1.5 text-xs">
      <span class="rounded bg-ink px-1.5 py-0.5 font-mono text-[11px] font-medium text-white">{{ selectedDiff.addressLabel }}</span>
      <span class="text-mute">改前：</span>
      <span class="max-w-[34vw] truncate rounded bg-rose-50 px-1.5 py-0.5 text-rose-900" :title="selectedDiff.beforeValue || '（空）'">
        {{ selectedDiff.beforeValue || '（空）' }}
      </span>
      <span v-if="selectedDiff.beforeFormula" class="max-w-[24vw] truncate font-mono text-[11px] text-mute" :title="selectedDiff.beforeFormula">
        {{ selectedDiff.beforeFormula }}
      </span>
      <ArrowRight class="size-3.5 shrink-0 text-faint" />
      <span class="text-mute">改后：</span>
      <span class="max-w-[34vw] truncate rounded bg-emerald-50 px-1.5 py-0.5 font-medium text-emerald-950" :title="selectedDiff.afterValue || '（空）'">
        {{ selectedDiff.afterValue || '（空）' }}
      </span>
      <span v-if="selectedDiff.afterFormula" class="max-w-[24vw] truncate font-mono text-[11px] text-blue-800" :title="selectedDiff.afterFormula">
        {{ selectedDiff.afterFormula }}
      </span>
    </div>

    <!-- 主体：全宽左右双栏同步表格 -->
    <div class="grid min-h-0 flex-1 grid-cols-1 divide-y divide-line overflow-hidden md:grid-cols-2 md:divide-x md:divide-y-0">
      <!-- 左侧：改前 -->
      <section class="relative flex min-h-0 flex-col bg-white" aria-label="改前表格">
        <div class="flex h-7 shrink-0 items-center border-b border-line bg-paper/50 px-3 text-[11px] text-mute">
          <span class="flex items-center gap-1.5 font-medium text-ink">
            <Sheet class="size-3.5 text-mute" />改前
            <span v-if="currentPair?.leftName" class="font-normal text-mute">· {{ currentPair.leftName }}</span>
          </span>
        </div>

        <div class="relative min-h-0 flex-1">
          <div ref="leftHost" class="absolute inset-0" data-testid="compare-left-canvas" />

          <div v-if="!leftMeta && !leftLoading && !leftError" class="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-white px-6 text-center">
            <FileSpreadsheet class="size-9 text-faint" />
            <p class="text-sm font-medium text-ink">请先选择改前表格</p>
            <button type="button" class="btn-line text-xs" @click="leftFileInputRef?.click()">
              <Upload class="size-3.5" />选择本地 Excel / CSV
            </button>
          </div>

          <div v-if="leftLoading" class="absolute inset-0 z-20 flex flex-col items-center justify-center gap-2.5 bg-white/95" role="status">
            <LoaderCircle class="size-6 animate-spin text-mute" />
            <span class="text-xs text-mute">正在读取表格…</span>
          </div>

          <div v-else-if="leftError" class="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-white px-6 text-center">
            <CircleAlert class="size-7 text-mute" />
            <p class="max-w-md text-xs leading-5 text-mute">{{ leftError }}</p>
            <button v-if="props.initialLeft" type="button" class="btn-line !h-8 text-xs" @click="loadSide('left', props.initialLeft, false)">
              <RotateCw class="size-3.5" />重试
            </button>
          </div>

          <div v-else-if="hasBothFiles && currentPair?.kind === 'added'" class="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-paper/80">
            <div class="rounded-2xl border border-line bg-white px-5 py-4 text-center shadow-xs">
              <p class="text-sm font-semibold text-ink">改前文件无此工作表</p>
              <p class="mt-1 text-xs text-mute">「{{ currentPair.name }}」为改后新增的工作表</p>
            </div>
          </div>

          <div v-if="diffBusy && hasBothFiles" class="absolute inset-0 z-20 flex items-center justify-center bg-white/65" role="status">
            <LoaderCircle class="size-6 animate-spin text-mute" />
          </div>
        </div>
      </section>

      <!-- 右侧：改后 或 URL 输入区 -->
      <section class="relative flex min-h-0 flex-col bg-white" aria-label="改后表格">
        <div class="flex h-7 shrink-0 items-center border-b border-line bg-paper/50 px-3 text-[11px] text-mute">
          <span class="flex items-center gap-1.5 font-medium text-ink">
            <Columns2 class="size-3.5 text-mute" />改后
            <span v-if="currentPair?.rightName" class="font-normal text-mute">· {{ currentPair.rightName }}</span>
          </span>
        </div>

        <div class="relative min-h-0 flex-1">
          <div v-if="rightMeta" ref="rightHost" class="absolute inset-0" data-testid="compare-right-canvas" />

          <div v-else-if="!rightLoading" class="absolute inset-0 z-10 flex items-center justify-center bg-paper/35 p-6">
            <div class="w-full max-w-md rounded-2xl border border-line bg-white p-6 shadow-xs">
              <div class="flex items-center gap-2.5">
                <span class="flex size-9 items-center justify-center rounded-xl bg-paper text-ink">
                  <Link2 class="size-4" />
                </span>
                <div>
                  <h2 class="text-sm font-semibold text-ink">输入要对比的另一个表格链接</h2>
                  <p class="text-xs text-mute">打开另一个表格的浏览页，复制浏览器地址栏粘贴到下方</p>
                </div>
              </div>

              <form class="mt-4 space-y-3" @submit.prevent="submitRightUrl">
                <input
                  v-model="urlInput"
                  type="text"
                  placeholder="例如：http://.../preview/f_xxxxxxxxxx 或直接粘贴文件 ID"
                  class="field !py-2 text-xs"
                  autofocus
                >
                <p v-if="urlInputError || rightError" role="alert" class="text-xs leading-5 text-error">
                  {{ urlInputError || rightError }}
                </p>
                <button type="submit" class="btn-ink w-full !py-2 text-xs" :disabled="!urlInput.trim() || rightLoading">
                  <GitCompareArrows class="size-4" />开始比对
                </button>
              </form>

              <div class="my-4 flex items-center gap-3 text-[11px] text-faint">
                <span class="h-px flex-1 bg-line" />
                <span>或者</span>
                <span class="h-px flex-1 bg-line" />
              </div>

              <button type="button" class="btn-line w-full !py-2 text-xs" @click="fileInputRef?.click()">
                <Upload class="size-3.5" />从电脑选择本地 Excel / CSV 文件
              </button>
            </div>
          </div>

          <div v-if="rightLoading" class="absolute inset-0 z-20 flex flex-col items-center justify-center gap-2.5 bg-white/95" role="status">
            <LoaderCircle class="size-6 animate-spin text-mute" />
            <span class="text-xs text-mute">正在读取并比对…</span>
          </div>

          <div v-else-if="hasBothFiles && currentPair?.kind === 'deleted'" class="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-paper/80">
            <div class="rounded-2xl border border-line bg-white px-5 py-4 text-center shadow-xs">
              <p class="text-sm font-semibold text-ink">改后已删除此工作表</p>
              <p class="mt-1 text-xs text-mute">「{{ currentPair.name }}」仅存在于改前文件</p>
            </div>
          </div>

          <div v-else-if="diffError && hasBothFiles" class="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-white px-6 text-center">
            <CircleAlert class="size-7 text-mute" />
            <p class="max-w-md text-xs leading-5 text-mute">{{ diffError }}</p>
            <button type="button" class="btn-line !h-8 text-xs" @click="runDiffForCurrentSheet()">
              <RotateCw class="size-3.5" />重新比对
            </button>
          </div>

          <div v-if="diffBusy && hasBothFiles" class="absolute inset-0 z-20 flex items-center justify-center bg-white/65" role="status">
            <LoaderCircle class="size-6 animate-spin text-mute" />
          </div>
        </div>
      </section>
    </div>

    <!-- 底部极简工作表标签栏 -->
    <footer class="flex shrink-0 items-stretch border-t border-line bg-paper px-2">
      <nav v-if="hasBothFiles" class="flex min-w-0 flex-1 items-stretch gap-0.5 overflow-x-auto" role="tablist" aria-label="比对工作表">
        <button
          v-for="pair in sheetPairs"
          :key="pair.key"
          type="button"
          role="tab"
          :aria-selected="currentPair?.key === pair.key"
          class="sheet-tab"
          :class="{ 'is-on': currentPair?.key === pair.key }"
          :title="pair.name"
          @click="selectPair(pair.key)"
        >
          <Plus v-if="pair.kind === 'added'" class="size-3 shrink-0 text-emerald-600" />
          <Minus v-else-if="pair.kind === 'deleted'" class="size-3 shrink-0 text-rose-600" />
          <span class="truncate">{{ pair.name }}</span>

          <span v-if="pair.kind === 'added'" class="rounded bg-emerald-100 px-1 py-0.5 text-[10px] font-medium text-emerald-800">新增</span>
          <span v-else-if="pair.kind === 'deleted'" class="rounded bg-rose-100 px-1 py-0.5 text-[10px] font-medium text-rose-800">删除</span>
          <template v-else>
            <LoaderCircle v-if="pairSummaries[pair.key] === 'loading'" class="size-3 shrink-0 animate-spin text-faint" />
            <span
              v-else-if="pairSummaries[pair.key] && typeof pairSummaries[pair.key] === 'object' && (pairSummaries[pair.key] as SheetDiffStats).total > 0"
              class="rounded-full bg-amber-100 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-amber-900"
            >
              {{ (pairSummaries[pair.key] as SheetDiffStats).total }}
            </span>
            <Check
              v-else-if="pairSummaries[pair.key] && typeof pairSummaries[pair.key] === 'object'"
              class="size-3 shrink-0 text-emerald-600"
              title="无差异"
            />
          </template>
        </button>
      </nav>

      <nav v-else-if="leftVisibleTabs.length" class="flex min-w-0 flex-1 items-stretch gap-0.5 overflow-x-auto" role="tablist" aria-label="工作表">
        <button
          v-for="sheet in leftVisibleTabs"
          :key="sheet.index"
          type="button"
          role="tab"
          :aria-selected="singleLeftSheet === sheet.index"
          class="sheet-tab"
          :class="{ 'is-on': singleLeftSheet === sheet.index }"
          @click="showSingleSheet('left', sheet.index)"
        >
          <span class="truncate">{{ sheet.name }}</span>
        </button>
      </nav>
    </footer>

    <input ref="fileInputRef" type="file" accept=".xlsx,.xlsm,.csv" class="hidden" @change="onPickLocalFile('right', $event)">
    <input ref="leftFileInputRef" type="file" accept=".xlsx,.xlsm,.csv" class="hidden" @change="onPickLocalFile('left', $event)">
  </main>
</template>

<style scoped>
.workbook-compare :deep(.univer-workbench) { height: 100%; }
.sheet-tab {
  display: inline-flex;
  max-width: 16rem;
  flex: none;
  align-items: center;
  gap: 5px;
  border-radius: 0 0 8px 8px;
  padding: 7px 12px;
  color: #6b6b7a;
  font-size: 12px;
  white-space: nowrap;
  transition: background-color .15s, color .15s;
}
.sheet-tab:hover { color: #14141c; }
.sheet-tab.is-on { background: #fff; color: #14141c; font-weight: 600; box-shadow: inset 0 -2px 0 #14141c; }
</style>

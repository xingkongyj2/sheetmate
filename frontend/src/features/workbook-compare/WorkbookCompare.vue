<script setup lang="ts">
/**
 * 极简双栏表格比对页：
 * - 顶部居中展示左右两个文件名，点击可从当前聊天页面的输入/输出文件中切换，也可粘贴任意表格链接；
 * - 可选当前聊天页面「工作区面板 → 文件」中的输入与输出表格，或任意公开的 .xlsx / .xlsm / .csv 链接（由后端代取）；
 * - 默认保留原样式并高亮差异，左右双栏同步滚动与选区联动。
 */
import {
  ArrowLeftRight,
  ArrowRight,
  Check,
  ChevronDown,
  CircleAlert,
  FileCheck2,
  LoaderCircle,
  RotateCw,
  Sheet,
} from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { conversationApi, fileApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo } from '@/api/types'
import { isImageFile } from '@/composables/attachmentFiles'
import { executionFileGroups, executionFileSize } from '@/composables/executionPresentation'

import type { SheetMeta } from '../workbook-preview/excel'
import ZoomControl from '../workbook-preview/ZoomControl.vue'
import type { CellPos, CompareViewer, ScrollSyncState } from './compare-viewer'
import CompareUrlInput from './CompareUrlInput.vue'
import { buildSheetPairs } from './diff'
import type { CellDiffEntry, CompareSource, DiffOptions, SheetDiffResult, SheetPair } from './types'
import { isSameCompareSource } from './url'
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
  conversationId: string | null
}>()

const emit = defineEmits<{
  'update-sources': [payload: { left: CompareSource | null; right: CompareSource | null; conversationId?: string | null }]
}>()

interface SideFileMeta {
  name: string
  size: number
  source: CompareSource
  /** 当前对话里的文件 id；链接 / 本地来源的表格没有 */
  fileId: string | null
}

const MAX_COMPARE_BYTES = 25 * 1024 * 1024

interface CandidateGroup {
  key: string
  label: string
  files: FileInfo[]
}

// ---- 当前聊天会话的文件列表（与工作区面板「文件」Tab 一致） ----
const activeConversationId = ref<string | null>(props.conversationId)
const conversationFiles = ref<FileInfo[]>([])
const filesLoading = ref(false)
const filesError = ref('')

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

// ---- 顶部居中下拉选择器 ----
const openMenu = ref<CompareSide | null>(null)
const switcherRef = ref<HTMLElement>()

// ---- 比对结果 ----
const activePairKey = ref('')
const diffBusy = ref(false)
const diffError = ref('')
const diffResult = ref<SheetDiffResult | null>(null)
const singleLeftSheet = ref(0)
const selectedDiff = ref<CellDiffEntry | null>(null)

// ---- DOM 与 Univer 实例 ----
const leftHost = ref<HTMLElement>()
const rightHost = ref<HTMLElement>()
const zoom = ref(1)
let leftViewer: CompareViewer | null = null
let rightViewer: CompareViewer | null = null
let leftScrollUnsub: (() => void) | null = null
let rightScrollUnsub: (() => void) | null = null
let leftSelUnsub: (() => void) | null = null
let rightSelUnsub: (() => void) | null = null
let leftZoomUnsub: (() => void) | null = null
let rightZoomUnsub: (() => void) | null = null

let worker: CompareWorkerClient | null = null
let viewerModulePromise: Promise<typeof import('./compare-viewer')> | null = null
let leftAbort: AbortController | null = null
let rightAbort: AbortController | null = null
let diffRequestSeq = 0
let singleRequestSeq = 0
let filesRequestSeq = 0

function isComparableFile(file: FileInfo): boolean {
  return !isImageFile(file.filename, file.media_type) && /\.(xlsx|xlsm|csv)$/i.test(file.filename)
}

const candidateGroups = computed<CandidateGroup[]>(() => {
  return executionFileGroups(conversationFiles.value)
    .map((group) => ({
      key: group.key,
      label: group.label,
      files: group.files.filter(isComparableFile),
    }))
    .filter((group) => group.files.length > 0)
})

const hasOtherCandidateForRight = computed(() =>
  candidateGroups.value.some((g) => g.files.some((f) => f.file_id !== leftMeta.value?.fileId)),
)

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
    leftZoomUnsub = leftViewer.onZoom((ratio: number) => {
      zoom.value = ratio
      if (hasBothFiles.value) rightViewer?.setZoom(ratio)
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
    rightZoomUnsub = rightViewer.onZoom((ratio: number) => {
      zoom.value = ratio
      if (hasBothFiles.value) leftViewer?.setZoom(ratio)
    })
  }
  return rightViewer
}

function disposeRightViewer() {
  rightScrollUnsub?.()
  rightSelUnsub?.()
  rightZoomUnsub?.()
  rightScrollUnsub = null
  rightSelUnsub = null
  rightZoomUnsub = null
  rightViewer?.dispose()
  rightViewer = null
  rightHost.value?.replaceChildren()
}

function applyZoom(ratio: number) {
  zoom.value = ratio
  leftViewer?.setZoom(ratio)
  rightViewer?.setZoom(ratio)
}

function onTabsWheel(event: WheelEvent) {
  const el = event.currentTarget as HTMLElement | null
  if (!el || el.scrollWidth <= el.clientWidth) return
  if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
    event.preventDefault()
    el.scrollLeft += event.deltaY
  }
}

function handleSelectionChange(pos: CellPos) {
  const key = `${pos.row}:${pos.col}`
  selectedDiff.value = diffResult.value?.cellDiffMap[key] ?? null
}

async function loadConversationFiles(convId: string) {
  const req = ++filesRequestSeq
  activeConversationId.value = convId
  filesLoading.value = true
  filesError.value = ''
  try {
    const detail = await conversationApi.get(convId)
    if (req !== filesRequestSeq) return
    conversationFiles.value = detail.files ?? []
  } catch (err) {
    if (req !== filesRequestSeq) return
    filesError.value = errorText(err)
  } finally {
    if (req === filesRequestSeq) filesLoading.value = false
  }
}

function validateFileName(filename: string, mediaType?: string) {
  if (isImageFile(filename, mediaType)) {
    throw new Error(`「${filename}」是图片文件，暂不支持表格比对。`)
  }
  if (/\.xls$/i.test(filename)) {
    throw new Error(`「${filename}」是旧版 .xls 格式，暂不支持在线表格比对。`)
  }
  if (!/\.(xlsx|xlsm|csv)$/i.test(filename)) {
    throw new Error(`「${filename}」不是支持的表格格式。`)
  }
}

async function loadSide(side: CompareSide, source: CompareSource, syncUrl = true) {
  if (source.kind === 'file') {
    const msg = '暂不支持这种表格来源。'
    if (side === 'left') leftError.value = msg
    else rightError.value = msg
    return
  }

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
  }
  const signal = (isLeft ? leftAbort : rightAbort)!.signal

  try {
    void loadViewerModule()
    let name: string
    let size: number
    let fileId: string | null = null
    let buffer: ArrayBuffer

    if (source.kind === 'remote') {
      const info = await fileApi.get(source.fileId)
      if (signal.aborted) return

      // 若 URL 未携带 conversation，从文件所属会话自动补全并拉取当前对话文件列表
      if (!activeConversationId.value && info.conversation_id) {
        void loadConversationFiles(info.conversation_id)
      }

      validateFileName(info.filename, info.media_type)
      if (info.size > MAX_COMPARE_BYTES) throw new Error('文件超过 25 MB，暂不支持在线比对。')
      buffer = await fileApi.content(info.file_id, signal)
      name = info.filename
      size = info.size
      fileId = info.file_id
    } else if (source.kind === 'url') {
      // 任意表格链接：由后端代取（避开浏览器跨域），只读不入库
      const fetched = await fileApi.fetchUrl(source.url, signal)
      validateFileName(fetched.filename)
      buffer = fetched.buffer
      name = fetched.filename
      size = buffer.byteLength
    } else {
      const resp = await fetch(source.blobUrl, { signal })
      buffer = await resp.arrayBuffer()
      name = source.name
      size = buffer.byteLength
      validateFileName(name)
    }
    if (signal.aborted) return
    if (buffer.byteLength > MAX_COMPARE_BYTES) throw new Error('文件超过 25 MB，暂不支持在线比对。')

    const opened = await ensureWorker().open(side, buffer, name)
    if (signal.aborted) return

    const meta: SideFileMeta = { name, size, source, fileId }

    if (isLeft) {
      leftMeta.value = meta
      leftSheets.value = opened.sheets
      leftActiveIndex.value = opened.active
      singleLeftSheet.value = opened.active
    } else {
      rightMeta.value = meta
      rightSheets.value = opened.sheets
      rightActiveIndex.value = opened.active
    }

    diffResult.value = null
    selectedDiff.value = null

    if (syncUrl) {
      emit('update-sources', {
        left: leftMeta.value?.source ?? null,
        right: rightMeta.value?.source ?? null,
        conversationId: activeConversationId.value,
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
    if (req !== singleRequestSeq || hasBothFiles.value) return
    if (zoom.value !== 1) viewer.setZoom(zoom.value)
    else zoom.value = viewer.getZoom()
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

    const [lv, rv] = await Promise.all([ensureLeftViewer(), ensureRightViewer()])
    if (req !== diffRequestSeq) return

    await Promise.all([
      lv?.show(result.leftWorkbook),
      rv?.show(result.rightWorkbook),
    ])
    if (req !== diffRequestSeq) return
    if (zoom.value !== 1) {
      lv?.setZoom(zoom.value)
      rv?.setZoom(zoom.value)
    }
  } catch (err) {
    if (req !== diffRequestSeq) return
    diffError.value = errorText(err)
  } finally {
    if (req === diffRequestSeq) diffBusy.value = false
  }
}

function selectPair(key: string, event?: Event) {
  ;(event?.currentTarget as HTMLElement | null)?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  if (key === activePairKey.value) return
  activePairKey.value = key
  void runDiffForCurrentSheet()
}

function selectSingleLeftTab(index: number, event?: Event) {
  ;(event?.currentTarget as HTMLElement | null)?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  if (index !== singleLeftSheet.value || leftError.value) {
    void showSingleSheet('left', index)
  }
}

function chooseFileForSide(side: CompareSide, file: FileInfo) {
  const otherId = side === 'left' ? rightMeta.value?.fileId : leftMeta.value?.fileId
  if (otherId && file.file_id === otherId) return
  openMenu.value = null
  const currentId = side === 'left' ? leftMeta.value?.fileId : rightMeta.value?.fileId
  if (currentId === file.file_id) return
  void loadSide(side, { kind: 'remote', fileId: file.file_id }, true)
}

function toggleMenu(side: CompareSide) {
  openMenu.value = openMenu.value === side ? null : side
}

/** 粘贴链接加载：本站文件、本地文件或任意在线表格都走这里 */
function chooseSourceForSide(side: CompareSide, source: CompareSource) {
  openMenu.value = null
  const otherSource = side === 'left' ? rightMeta.value?.source : leftMeta.value?.source
  if (isSameCompareSource(source, otherSource ?? null)) {
    const msg = '这份表格已在另一侧打开，请选择不同的表格。'
    if (side === 'left') leftError.value = msg
    else rightError.value = msg
    return
  }
  void loadSide(side, source, true)
}

/** 已有表格时，换表失败的原因显示在顶部提示条（不遮挡现有内容） */
const inlineError = computed(() => (rightMeta.value && rightError.value) || (leftMeta.value && leftError.value) || '')

function dismissInlineError() {
  if (leftMeta.value) leftError.value = ''
  if (rightMeta.value) rightError.value = ''
}

async function swapSides() {
  if (!leftMeta.value || !rightMeta.value || leftLoading.value || rightLoading.value || diffBusy.value) return
  openMenu.value = null
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
    conversationId: activeConversationId.value,
  })
  updateDocumentTitle()
  await runDiffForCurrentSheet()
}

function updateDocumentTitle() {
  if (leftMeta.value && rightMeta.value) {
    document.title = `${leftMeta.value.name} 对比 ${rightMeta.value.name}`
  } else if (leftMeta.value) {
    document.title = `比对 · ${leftMeta.value.name}`
  } else {
    document.title = '表格比对'
  }
}

function closeMenuOutside(event: Event) {
  if (!openMenu.value) return
  if (event instanceof KeyboardEvent) {
    if (event.key === 'Escape') openMenu.value = null
    return
  }
  if (!switcherRef.value?.contains(event.target as Node)) openMenu.value = null
}

watch(() => props.conversationId, (next) => {
  if (next && next !== activeConversationId.value) {
    void loadConversationFiles(next)
  }
})

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
  window.addEventListener('pointerdown', closeMenuOutside)
  window.addEventListener('keydown', closeMenuOutside)
  if (props.conversationId) void loadConversationFiles(props.conversationId)
  if (props.initialLeft) void loadSide('left', props.initialLeft, false)
  if (props.initialRight) void loadSide('right', props.initialRight, false)
})

onBeforeUnmount(() => {
  diffRequestSeq++
  singleRequestSeq++
  filesRequestSeq++
  leftAbort?.abort()
  rightAbort?.abort()
  window.removeEventListener('pointerdown', closeMenuOutside)
  window.removeEventListener('keydown', closeMenuOutside)
  leftScrollUnsub?.()
  leftSelUnsub?.()
  leftZoomUnsub?.()
  leftViewer?.dispose()
  disposeRightViewer()
  worker?.dispose()
})
</script>

<template>
  <main class="workbook-compare flex h-dvh w-full flex-col overflow-hidden bg-white">
    <!-- 顶部居中双文件栏：克制纤细，点击文件名可直接在当前会话的输入/输出文件中切换 -->
    <header class="relative z-30 flex h-9 shrink-0 items-center justify-center border-b border-line bg-white px-4">
      <div ref="switcherRef" class="flex min-w-0 max-w-full items-center justify-center gap-1 sm:gap-1.5">
        <!-- 左侧文件下拉触发器 -->
        <div class="relative min-w-0">
          <button
            type="button"
            class="file-trigger"
            :class="{ 'is-open': openMenu === 'left' }"
            :title="leftMeta?.name || '选择改前表格'"
            aria-haspopup="menu"
            :aria-expanded="openMenu === 'left'"
            @click="toggleMenu('left')"
          >
            <span class="shrink-0 text-[11px] font-normal text-faint">改前</span>
            <LoaderCircle v-if="leftLoading" class="size-3.5 shrink-0 animate-spin text-mute" />
            <span v-else class="min-w-0 max-w-[30vw] truncate sm:max-w-[260px]" :class="leftMeta ? 'font-medium text-ink' : 'text-faint'">
              {{ leftMeta?.name || '选择表格' }}
            </span>
            <ChevronDown class="size-3.5 shrink-0 text-faint transition-transform" :class="{ 'rotate-180': openMenu === 'left' }" />
          </button>

          <!-- 左侧文件下拉菜单 -->
          <div v-if="openMenu === 'left'" class="file-menu left-0 sm:left-1/2 sm:-translate-x-1/2" role="menu">
            <div v-for="group in candidateGroups" :key="group.key" class="mb-1.5 last:mb-0">
              <div class="px-2.5 py-1 text-[10px] font-medium tracking-wide text-faint">{{ group.label }}</div>
              <button
                v-for="file in group.files"
                :key="file.file_id"
                type="button"
                role="menuitem"
                class="file-menu-item"
                :class="{
                  'is-selected': leftMeta?.fileId === file.file_id,
                  'is-disabled': rightMeta?.fileId === file.file_id,
                }"
                :disabled="rightMeta?.fileId === file.file_id"
                @click="chooseFileForSide('left', file)"
              >
                <FileCheck2 v-if="file.kind === 'expected'" class="size-3.5 shrink-0 text-mute" />
                <Sheet v-else class="size-3.5 shrink-0" :class="file.kind === 'output' ? 'text-ink' : 'text-mute'" />
                <span class="min-w-0 flex-1 truncate" :title="file.filename">{{ file.filename }}</span>
                <Check v-if="leftMeta?.fileId === file.file_id" class="size-3.5 shrink-0 text-ink" />
                <span v-else-if="rightMeta?.fileId === file.file_id" class="shrink-0 text-[10px] text-faint">当前改后</span>
              </button>
            </div>
            <div class="menu-url" :class="{ 'has-files': candidateGroups.length }">
              <CompareUrlInput :busy="leftLoading" @submit="chooseSourceForSide('left', $event)" />
            </div>
          </div>
        </div>

        <!-- 中央对调按钮 -->
        <button
          type="button"
          class="flex size-6 shrink-0 items-center justify-center rounded-full text-faint transition hover:bg-black/[0.045] hover:text-ink disabled:cursor-default disabled:opacity-35 disabled:hover:bg-transparent disabled:hover:text-faint"
          :disabled="!hasBothFiles || diffBusy"
          title="对调左右文件"
          aria-label="对调左右文件"
          @click="swapSides"
        >
          <ArrowLeftRight class="size-3" />
        </button>

        <!-- 右侧文件下拉触发器 -->
        <div class="relative min-w-0">
          <button
            type="button"
            class="file-trigger"
            :class="{ 'is-open': openMenu === 'right' }"
            :title="rightMeta?.name || '选择改后表格'"
            aria-haspopup="menu"
            :aria-expanded="openMenu === 'right'"
            @click="toggleMenu('right')"
          >
            <span class="shrink-0 text-[11px] font-normal text-faint">改后</span>
            <LoaderCircle v-if="rightLoading" class="size-3.5 shrink-0 animate-spin text-mute" />
            <span v-else class="min-w-0 max-w-[30vw] truncate sm:max-w-[260px]" :class="rightMeta ? 'font-medium text-ink' : 'text-mute'">
              {{ rightMeta?.name || '选择对比表格' }}
            </span>
            <ChevronDown class="size-3.5 shrink-0 text-faint transition-transform" :class="{ 'rotate-180': openMenu === 'right' }" />
          </button>

          <!-- 右侧文件下拉菜单 -->
          <div v-if="openMenu === 'right'" class="file-menu right-0 sm:left-1/2 sm:right-auto sm:-translate-x-1/2" role="menu">
            <div v-for="group in candidateGroups" :key="group.key" class="mb-1.5 last:mb-0">
              <div class="px-2.5 py-1 text-[10px] font-medium tracking-wide text-faint">{{ group.label }}</div>
              <button
                v-for="file in group.files"
                :key="file.file_id"
                type="button"
                role="menuitem"
                class="file-menu-item"
                :class="{
                  'is-selected': rightMeta?.fileId === file.file_id,
                  'is-disabled': leftMeta?.fileId === file.file_id,
                }"
                :disabled="leftMeta?.fileId === file.file_id"
                @click="chooseFileForSide('right', file)"
              >
                <FileCheck2 v-if="file.kind === 'expected'" class="size-3.5 shrink-0 text-mute" />
                <Sheet v-else class="size-3.5 shrink-0" :class="file.kind === 'output' ? 'text-ink' : 'text-mute'" />
                <span class="min-w-0 flex-1 truncate" :title="file.filename">{{ file.filename }}</span>
                <Check v-if="rightMeta?.fileId === file.file_id" class="size-3.5 shrink-0 text-ink" />
                <span v-else-if="leftMeta?.fileId === file.file_id" class="shrink-0 text-[10px] text-faint">当前改前</span>
              </button>
            </div>
            <div class="menu-url" :class="{ 'has-files': candidateGroups.length }">
              <CompareUrlInput :busy="rightLoading" @submit="chooseSourceForSide('right', $event)" />
            </div>
          </div>
        </div>
      </div>
    </header>

    <!-- 换表失败（现有内容保持不变）：顶部提示条 -->
    <div v-if="inlineError" role="alert" class="flex shrink-0 items-center justify-center gap-2 border-b border-rose-100 bg-rose-50 px-4 py-1.5 text-xs text-rose-700">
      <CircleAlert class="size-3.5 shrink-0" />
      <span class="min-w-0 truncate" :title="inlineError">{{ inlineError }}</span>
      <button type="button" class="shrink-0 font-medium underline-offset-2 hover:underline" @click="dismissInlineError">知道了</button>
    </div>

    <!-- 点中差异单元格时，轻展示一行改前/改后值与公式对照 -->
    <div v-if="hasBothFiles && selectedDiff" class="flex shrink-0 flex-wrap items-center justify-center gap-2 border-b border-line bg-[#fafaf9] px-4 py-1.5 text-xs">
      <span class="rounded bg-ink px-1.5 py-0.5 font-mono text-[11px] font-medium text-white">{{ selectedDiff.addressLabel }}</span>
      <span class="text-mute">改前</span>
      <span class="max-w-[32vw] truncate rounded bg-rose-50 px-1.5 py-0.5 text-rose-900" :title="selectedDiff.beforeValue || '（空）'">
        {{ selectedDiff.beforeValue || '（空）' }}
      </span>
      <span v-if="selectedDiff.beforeFormula" class="max-w-[22vw] truncate font-mono text-[11px] text-mute" :title="selectedDiff.beforeFormula">
        {{ selectedDiff.beforeFormula }}
      </span>
      <ArrowRight class="size-3.5 shrink-0 text-faint" />
      <span class="text-mute">改后</span>
      <span class="max-w-[32vw] truncate rounded bg-emerald-50 px-1.5 py-0.5 font-medium text-emerald-950" :title="selectedDiff.afterValue || '（空）'">
        {{ selectedDiff.afterValue || '（空）' }}
      </span>
      <span v-if="selectedDiff.afterFormula" class="max-w-[22vw] truncate font-mono text-[11px] text-blue-800" :title="selectedDiff.afterFormula">
        {{ selectedDiff.afterFormula }}
      </span>
    </div>

    <!-- 主体：全宽左右双栏同步表格 -->
    <div class="grid min-h-0 flex-1 grid-cols-1 divide-y divide-line overflow-hidden md:grid-cols-2 md:divide-x md:divide-y-0">
      <!-- 左侧：改前 -->
      <section class="relative min-h-0 bg-white" aria-label="改前表格">
        <div ref="leftHost" class="absolute inset-0" data-testid="compare-left-canvas" />

        <!-- 左侧未选择文件时：展示当前会话文件选择器，也可粘贴任意表格链接 -->
        <div v-if="!leftMeta && !leftLoading" class="absolute inset-0 z-10 flex items-center justify-center bg-[#fafaf9] p-6">
          <div class="w-full max-w-[400px] rounded-2xl border border-line bg-white p-5 shadow-[0_1px_3px_rgba(0,0,0,0.03)]">
            <h2 class="text-sm font-semibold text-ink">选择改前表格</h2>

            <div v-if="leftError" role="alert" class="mt-3 flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
              <span class="min-w-0 flex-1">{{ leftError }}</span>
              <button v-if="props.initialLeft" type="button" class="inline-flex shrink-0 items-center gap-1 font-medium underline-offset-2 hover:underline" @click="loadSide('left', props.initialLeft, false)">
                <RotateCw class="size-3" />重试
              </button>
            </div>

            <div v-if="filesLoading" class="flex items-center justify-center gap-2 py-10 text-xs text-mute">
              <LoaderCircle class="size-4 animate-spin" />正在获取当前对话文件…
            </div>
            <div v-else-if="filesError" class="py-8 text-center text-xs text-error">{{ filesError }}</div>
            <div v-else-if="!activeConversationId" class="py-6 text-center text-xs leading-5 text-mute">
              未关联聊天对话，可在下方粘贴表格链接。
            </div>
            <div v-else-if="!candidateGroups.length" class="py-6 text-center text-xs text-mute">
              当前对话中暂无可比对的表格文件。
            </div>
            <div v-else class="mt-4 max-h-[40vh] space-y-4 overflow-y-auto pr-0.5">
              <div v-for="group in candidateGroups" :key="group.key">
                <div class="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-faint">
                  <span>{{ group.label }}</span>
                  <span class="tabular-nums">{{ group.files.length }}</span>
                </div>
                <div class="space-y-1.5">
                  <button
                    v-for="file in group.files"
                    :key="file.file_id"
                    type="button"
                    class="picker-row"
                    :class="{ 'is-disabled': rightMeta?.fileId === file.file_id }"
                    :disabled="rightMeta?.fileId === file.file_id"
                    @click="chooseFileForSide('left', file)"
                  >
                    <FileCheck2 v-if="file.kind === 'expected'" class="size-4 shrink-0 text-[#4a4743]" />
                    <Sheet v-else class="size-4 shrink-0" :class="file.kind === 'output' ? 'text-ink' : 'text-[#4a4743]'" />
                    <div class="min-w-0 flex-1">
                      <div class="truncate text-[13px] font-medium text-ink" :title="file.filename">{{ file.filename }}</div>
                      <div v-if="file.size" class="text-[11px] text-faint">{{ executionFileSize(file.size) }}</div>
                    </div>
                    <span v-if="rightMeta?.fileId === file.file_id" class="shrink-0 text-[11px] text-faint">当前改后</span>
                    <ArrowRight v-else class="picker-arrow size-3.5 shrink-0 text-faint" />
                  </button>
                </div>
              </div>
            </div>

            <div class="mt-4 border-t border-line pt-3.5">
              <CompareUrlInput :busy="leftLoading" @submit="chooseSourceForSide('left', $event)" />
            </div>
          </div>
        </div>

        <div v-if="leftLoading" class="absolute inset-0 z-20 flex flex-col items-center justify-center gap-2.5 bg-white/95" role="status">
          <LoaderCircle class="size-6 animate-spin text-mute" />
          <span class="text-xs text-mute">正在读取表格…</span>
        </div>

        <div v-else-if="hasBothFiles && currentPair?.kind === 'added'" class="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-[#fafaf9]/85">
          <div class="rounded-2xl border border-line bg-white px-5 py-4 text-center shadow-xs">
            <p class="text-sm font-semibold text-ink">改前文件无此工作表</p>
            <p class="mt-1 text-xs text-mute">「{{ currentPair.name }}」为改后新增的工作表</p>
          </div>
        </div>

        <div v-if="diffBusy && hasBothFiles" class="absolute inset-0 z-20 flex items-center justify-center bg-white/65" role="status">
          <LoaderCircle class="size-6 animate-spin text-mute" />
        </div>
      </section>

      <!-- 右侧：改后 或 当前对话文件选择面板 -->
      <section class="relative min-h-0 bg-white" aria-label="改后表格">
        <div v-if="rightMeta" ref="rightHost" class="absolute inset-0" data-testid="compare-right-canvas" />

        <div v-else-if="!rightLoading" class="absolute inset-0 z-10 flex items-center justify-center bg-[#fafaf9] p-6">
          <div class="w-full max-w-[400px] rounded-2xl border border-line bg-white p-5 shadow-[0_1px_3px_rgba(0,0,0,0.03)]">
            <h2 class="text-sm font-semibold text-ink">选择对比表格</h2>

            <p v-if="rightError" role="alert" class="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{{ rightError }}</p>

            <div v-if="filesLoading" class="flex items-center justify-center gap-2 py-10 text-xs text-mute">
              <LoaderCircle class="size-4 animate-spin" />正在获取当前对话文件…
            </div>
            <div v-else-if="filesError" class="flex flex-col items-center gap-3 py-8 text-center">
              <p class="text-xs text-error">{{ filesError }}</p>
              <button v-if="activeConversationId" type="button" class="btn-line !h-8 text-xs" @click="loadConversationFiles(activeConversationId)">
                <RotateCw class="size-3.5" />重新加载
              </button>
            </div>
            <div v-else-if="!activeConversationId" class="py-6 text-center text-xs leading-5 text-mute">
              未关联聊天对话，可在下方粘贴表格链接。
            </div>
            <div v-else-if="!hasOtherCandidateForRight" class="py-6 text-center text-xs leading-5 text-mute">
              当前对话中暂无其他可比对的表格文件。
            </div>
            <div v-else class="mt-4 max-h-[40vh] space-y-4 overflow-y-auto pr-0.5">
              <div v-for="group in candidateGroups" :key="group.key">
                <div class="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-faint">
                  <span>{{ group.label }}</span>
                  <span class="tabular-nums">{{ group.files.length }}</span>
                </div>
                <div class="space-y-1.5">
                  <button
                    v-for="file in group.files"
                    :key="file.file_id"
                    type="button"
                    class="picker-row"
                    :class="{ 'is-disabled': leftMeta?.fileId === file.file_id }"
                    :disabled="leftMeta?.fileId === file.file_id"
                    @click="chooseFileForSide('right', file)"
                  >
                    <FileCheck2 v-if="file.kind === 'expected'" class="size-4 shrink-0 text-[#4a4743]" />
                    <Sheet v-else class="size-4 shrink-0" :class="file.kind === 'output' ? 'text-ink' : 'text-[#4a4743]'" />
                    <div class="min-w-0 flex-1">
                      <div class="truncate text-[13px] font-medium text-ink" :title="file.filename">{{ file.filename }}</div>
                      <div v-if="file.size" class="text-[11px] text-faint">{{ executionFileSize(file.size) }}</div>
                    </div>
                    <span v-if="leftMeta?.fileId === file.file_id" class="shrink-0 text-[11px] text-faint">当前改前</span>
                    <ArrowRight v-else class="picker-arrow size-3.5 shrink-0 text-faint" />
                  </button>
                </div>
              </div>
            </div>

            <div class="mt-4 border-t border-line pt-3.5">
              <CompareUrlInput :busy="rightLoading" @submit="chooseSourceForSide('right', $event)" />
            </div>
          </div>
        </div>

        <div v-if="rightLoading" class="absolute inset-0 z-20 flex flex-col items-center justify-center gap-2.5 bg-white/95" role="status">
          <LoaderCircle class="size-6 animate-spin text-mute" />
          <span class="text-xs text-mute">正在读取并比对…</span>
        </div>

        <div v-else-if="hasBothFiles && currentPair?.kind === 'deleted'" class="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-[#fafaf9]/85">
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
      </section>
    </div>

    <!-- 底部单行栏：左侧可左右滑动的 Sheet 标签，右侧固定宽度的缩放百分比控件 -->
    <footer class="relative z-30 flex h-10 shrink-0 items-center border-t border-line bg-[#fafaf9] px-2.5">
      <nav v-if="hasBothFiles" class="sheet-strip flex min-w-0 flex-1 items-center gap-1 overflow-x-auto py-1 pr-2" role="tablist" aria-label="比对工作表" @wheel="onTabsWheel">
        <button
          v-for="pair in sheetPairs"
          :key="pair.key"
          type="button"
          role="tab"
          :aria-selected="currentPair?.key === pair.key"
          class="sheet-tab"
          :class="{ 'is-on': currentPair?.key === pair.key }"
          :title="pair.kind === 'added' ? `${pair.name}（新增）` : pair.kind === 'deleted' ? `${pair.name}（已删除）` : pair.name"
          @click="selectPair(pair.key, $event)"
        >
          <span v-if="pair.kind === 'added'" class="size-1.5 shrink-0 rounded-full bg-emerald-500" />
          <span v-else-if="pair.kind === 'deleted'" class="size-1.5 shrink-0 rounded-full bg-rose-500" />
          <span class="truncate">{{ pair.name }}</span>
        </button>
      </nav>

      <nav v-else-if="leftVisibleTabs.length" class="sheet-strip flex min-w-0 flex-1 items-center gap-1 overflow-x-auto py-1 pr-2" role="tablist" aria-label="工作表" @wheel="onTabsWheel">
        <button
          v-for="sheet in leftVisibleTabs"
          :key="sheet.index"
          type="button"
          role="tab"
          :aria-selected="singleLeftSheet === sheet.index"
          class="sheet-tab"
          :class="{ 'is-on': singleLeftSheet === sheet.index }"
          @click="selectSingleLeftTab(sheet.index, $event)"
        >
          <span class="truncate">{{ sheet.name }}</span>
        </button>
      </nav>

      <div v-else class="flex-1" />

      <div v-if="leftMeta || rightMeta" class="flex shrink-0 items-center border-l border-line/80 pl-2.5">
        <ZoomControl
          :model-value="zoom"
          :disabled="diffBusy || leftLoading || rightLoading"
          @update:model-value="applyZoom"
        />
      </div>
    </footer>
  </main>
</template>

<style scoped>
.workbook-compare :deep(.univer-workbench) { height: 100%; }

/* 顶部居中文件名选择器：纯文字轻悬停，纤巧克制 */
.file-trigger {
  display: inline-flex;
  height: 26px;
  max-width: 100%;
  align-items: center;
  gap: 5px;
  border-radius: 7px;
  padding: 0 8px;
  font-size: 12.5px;
  transition: background-color .15s, color .15s;
}
.file-trigger:hover:not(:disabled),
.file-trigger.is-open {
  background-color: rgb(11 11 12 / 0.045);
}
.file-trigger:disabled {
  cursor: default;
}

.file-menu {
  position: absolute;
  top: calc(100% + 6px);
  z-index: 40;
  width: 296px;
  max-height: 360px;
  overflow-y: auto;
  border-radius: 14px;
  border: 1px solid var(--color-line);
  background: #fff;
  padding: 6px;
  box-shadow: 0 10px 28px rgb(0 0 0 / 0.08), 0 1px 3px rgb(0 0 0 / 0.04);
}
.file-menu-item {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 8px;
  border-radius: 9px;
  padding: 7px 10px;
  text-align: left;
  font-size: 12.5px;
  color: var(--color-ink);
  transition: background-color .12s;
}
.file-menu-item:hover:not(:disabled) {
  background: var(--color-paper);
}
.file-menu-item.is-selected {
  background: rgb(11 11 12 / 0.05);
  font-weight: 500;
}
.file-menu-item.is-disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.menu-url {
  padding: 8px 6px 4px;
}
.menu-url.has-files {
  margin-top: 4px;
  border-top: 1px solid var(--color-line);
}

/* 右侧/左侧文件选择卡片行 */
.picker-row {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 10px;
  border-radius: 12px;
  border: 1px solid var(--color-line);
  background: #fff;
  padding: 9px 12px;
  text-align: left;
  transition: border-color .15s, background-color .15s, box-shadow .15s;
}
.picker-row:hover:not(:disabled) {
  border-color: rgb(11 11 12 / 0.24);
  background: #fafaf9;
}
.picker-row:hover:not(:disabled) .picker-arrow {
  color: var(--color-ink);
  transform: translateX(2px);
}
.picker-arrow {
  transition: transform .15s, color .15s;
}
.picker-row.is-disabled {
  opacity: 0.48;
  background: #fafaf9;
  cursor: not-allowed;
}

/* 底部现代轻卡片式工作表标签 */
.sheet-strip {
  scrollbar-width: none;
}
.sheet-strip::-webkit-scrollbar {
  display: none;
}
.sheet-tab {
  display: inline-flex;
  height: 28px;
  max-width: 16rem;
  flex: none;
  align-items: center;
  gap: 6px;
  border-radius: 8px;
  padding: 0 11px;
  color: #6b6863;
  font-size: 12px;
  white-space: nowrap;
  transition: background-color .15s, color .15s, box-shadow .15s;
}
.sheet-tab:hover {
  background: rgb(11 11 12 / 0.045);
  color: #14141c;
}
.sheet-tab.is-on {
  background: #fff;
  color: #111111;
  font-weight: 500;
  box-shadow: 0 1px 2px rgb(0 0 0 / 0.06), 0 0 0 1px rgb(11 11 12 / 0.08);
}
</style>

import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core'
import zhCN from '@univerjs/preset-sheets-core/locales/zh-CN'
import { createUniver, LocaleType, type IWorkbookData } from '@univerjs/presets'

import '@univerjs/preset-sheets-core/lib/index.css'

import { autoFitSheetRows, createRenderedGate } from '@/features/workbook-preview/autoFitRows'

export interface ScrollSyncState {
  sheetViewStartRow: number
  sheetViewStartColumn: number
  offsetX: number
  offsetY: number
}

export interface CellPos {
  row: number
  col: number
}

export interface CompareViewer {
  show(snapshot: IWorkbookData, options?: { preserveScroll?: boolean }): Promise<void>
  getScrollState(): ScrollSyncState | null
  setScrollState(state: ScrollSyncState): void
  setZoom(ratio: number): void
  getZoom(): number
  focusCell(row: number, col: number, scroll?: boolean): void
  onScroll(handler: (state: ScrollSyncState) => void): () => void
  onSelection(handler: (pos: CellPos) => void): () => void
  onZoom(handler: (ratio: number) => void): () => void
  dispose(): void
}

/**
 * 专用于双栏比对的只读 Univer 查看器。
 * 隐藏编辑工具栏、公式栏和原生底栏，由比对页在同一行统一提供 Sheet 标签与双栏同步缩放。
 */
export function createCompareViewer(container: HTMLElement): CompareViewer {
  const { univer, univerAPI } = createUniver({
    locale: LocaleType.ZH_CN,
    locales: { [LocaleType.ZH_CN]: zhCN },
    presets: [UniverSheetsCorePreset({
      container,
      header: false,
      toolbar: false,
      contextMenu: false,
      disableAutoFocus: true,
      formulaBar: false,
      footer: false,
    })],
  })

  const renderedGate = createRenderedGate(univerAPI)
  let currentUnitId: string | undefined
  let currentSheetId: string | undefined
  let disposed = false
  let unitSeq = 0
  let suppressScroll = false
  let suppressSelection = false
  let suppressZoom = false
  let scrollReleaseTimer: ReturnType<typeof setTimeout> | undefined
  let selectionReleaseTimer: ReturnType<typeof setTimeout> | undefined
  let zoomReleaseTimer: ReturnType<typeof setTimeout> | undefined

  const scrollListeners = new Set<(state: ScrollSyncState) => void>()
  const selectionListeners = new Set<(pos: CellPos) => void>()
  const zoomListeners = new Set<(ratio: number) => void>()

  function readScrollState(): ScrollSyncState | null {
    const workbook = univerAPI.getActiveWorkbook()
    const sheet = workbook?.getActiveSheet()
    if (!sheet) return null
    const state = sheet.getScrollState()
    if (!state) return null
    return {
      sheetViewStartRow: state.sheetViewStartRow ?? 0,
      sheetViewStartColumn: state.sheetViewStartColumn ?? 0,
      offsetX: state.offsetX ?? 0,
      offsetY: state.offsetY ?? 0,
    }
  }

  const scrollSub = univerAPI.addEvent(univerAPI.Event.Scroll, () => {
    if (suppressScroll || !scrollListeners.size) return
    const state = readScrollState()
    if (!state) return
    for (const fn of scrollListeners) fn(state)
  })

  const selectionSub = univerAPI.addEvent(univerAPI.Event.SelectionChanged, (params) => {
    if (suppressSelection || !selectionListeners.size) return
    const first = params.selections?.[0]
    if (!first) return
    const pos: CellPos = { row: first.startRow, col: first.startColumn }
    for (const fn of selectionListeners) fn(pos)
  })

  const zoomSub = univerAPI.addEvent(univerAPI.Event.SheetZoomChanged, (params) => {
    if (suppressZoom || !zoomListeners.size) return
    const ratio = params.zoom
    if (typeof ratio === 'number' && Number.isFinite(ratio)) {
      for (const fn of zoomListeners) fn(ratio)
    }
  })

  function setZoom(ratio: number) {
    const sheet = univerAPI.getActiveWorkbook()?.getActiveSheet()
    if (!sheet) return
    const clamped = Math.min(2, Math.max(0.5, ratio))
    suppressZoom = true
    clearTimeout(zoomReleaseTimer)
    try {
      sheet.zoom(clamped)
    } finally {
      zoomReleaseTimer = setTimeout(() => { suppressZoom = false }, 50)
    }
  }

  function getZoom(): number {
    return univerAPI.getActiveWorkbook()?.getActiveSheet()?.getZoom() ?? 1
  }

  function setScrollState(state: ScrollSyncState) {
    if (!currentUnitId || !currentSheetId) return
    suppressScroll = true
    clearTimeout(scrollReleaseTimer)
    try {
      void univerAPI.executeCommand('sheet.command.scroll-view', {
        unitId: currentUnitId,
        sheetId: currentSheetId,
        sheetViewStartRow: state.sheetViewStartRow,
        sheetViewStartColumn: state.sheetViewStartColumn,
        offsetX: state.offsetX,
        offsetY: state.offsetY,
      })
    } finally {
      scrollReleaseTimer = setTimeout(() => { suppressScroll = false }, 40)
    }
  }

  function focusCell(row: number, col: number, scroll = true) {
    const workbook = univerAPI.getActiveWorkbook()
    const sheet = workbook?.getActiveSheet()
    if (!sheet) return
    const maxR = Math.max(0, sheet.getMaxRows() - 1)
    const maxC = Math.max(0, sheet.getMaxColumns() - 1)
    const safeRow = Math.min(Math.max(0, row), maxR)
    const safeCol = Math.min(Math.max(0, col), maxC)

    suppressSelection = true
    clearTimeout(selectionReleaseTimer)
    if (scroll) {
      suppressScroll = true
      clearTimeout(scrollReleaseTimer)
    }
    try {
      if (scroll) {
        // 留出少量上方和左侧上下文，避免目标格死贴画布最边缘
        sheet.scrollToCell(Math.max(0, safeRow - 3), Math.max(0, safeCol - 1))
      }
      const range = sheet.getRange(safeRow, safeCol, 1, 1)
      sheet.setActiveRange(range)
    } finally {
      selectionReleaseTimer = setTimeout(() => { suppressSelection = false }, 50)
      if (scroll) scrollReleaseTimer = setTimeout(() => { suppressScroll = false }, 80)
    }
  }

  return {
    async show(snapshot, options) {
      const savedScroll = options?.preserveScroll ? readScrollState() : null
      suppressScroll = true
      suppressSelection = true
      clearTimeout(scrollReleaseTimer)
      clearTimeout(selectionReleaseTimer)

      // 始终分配新的 unit id：Worker 缓存复用的同一快照或重试加载不会与尚未销毁的旧 unit 冲突
      const unitId = `${snapshot.id || 'cmp'}-${++unitSeq}`
      const nextSnapshot: IWorkbookData = {
        ...snapshot,
        id: unitId,
      }
      const workbook = univerAPI.createWorkbook(nextSnapshot)
      const previous = currentUnitId
      currentUnitId = workbook.getId()
      currentSheetId = workbook.getActiveSheet()?.getSheetId()
      if (previous) univerAPI.disposeUnit(previous)

      try {
        await renderedGate.wait()
        if (disposed || currentUnitId !== unitId) return
        autoFitSheetRows(workbook, nextSnapshot)
        workbook.setEditable(false)
        await workbook.getWorkbookPermission().setReadOnly()
      } finally {
        if (!disposed && currentUnitId === unitId) {
          if (savedScroll) {
            requestAnimationFrame(() => setScrollState(savedScroll))
          }
          scrollReleaseTimer = setTimeout(() => { suppressScroll = false }, 80)
          selectionReleaseTimer = setTimeout(() => { suppressSelection = false }, 80)
        }
      }
    },
    getScrollState: readScrollState,
    setScrollState,
    setZoom,
    getZoom,
    focusCell,
    onScroll(handler) {
      scrollListeners.add(handler)
      return () => scrollListeners.delete(handler)
    },
    onSelection(handler) {
      selectionListeners.add(handler)
      return () => selectionListeners.delete(handler)
    },
    onZoom(handler) {
      zoomListeners.add(handler)
      return () => zoomListeners.delete(handler)
    },
    dispose() {
      disposed = true
      renderedGate.dispose()
      clearTimeout(scrollReleaseTimer)
      clearTimeout(selectionReleaseTimer)
      clearTimeout(zoomReleaseTimer)
      scrollListeners.clear()
      selectionListeners.clear()
      zoomListeners.clear()
      scrollSub.dispose()
      selectionSub.dispose()
      zoomSub.dispose()
      univer.dispose()
    },
  }
}

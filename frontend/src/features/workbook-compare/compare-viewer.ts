import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core'
import zhCN from '@univerjs/preset-sheets-core/locales/zh-CN'
import { createUniver, LocaleType, type IWorkbookData } from '@univerjs/presets'

import '@univerjs/preset-sheets-core/lib/index.css'

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
  focusCell(row: number, col: number, scroll?: boolean): void
  onScroll(handler: (state: ScrollSyncState) => void): () => void
  onSelection(handler: (pos: CellPos) => void): () => void
  dispose(): void
}

/**
 * 专用于双栏比对的只读 Univer 查看器。
 * 隐藏重复的编辑工具栏、公式栏和原生 Sheet 标签栏，暴露滚动与选区同步钩子。
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
      footer: { sheetBar: false, statisticBar: false, menus: false, zoomSlider: true },
    })],
  })

  let currentUnitId: string | undefined
  let currentSheetId: string | undefined
  let suppressScroll = false
  let suppressSelection = false
  let scrollReleaseTimer: ReturnType<typeof setTimeout> | undefined
  let selectionReleaseTimer: ReturnType<typeof setTimeout> | undefined

  const scrollListeners = new Set<(state: ScrollSyncState) => void>()
  const selectionListeners = new Set<(pos: CellPos) => void>()

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

      const workbook = univerAPI.createWorkbook(snapshot)
      const previous = currentUnitId
      currentUnitId = workbook.getId()
      currentSheetId = workbook.getActiveSheet()?.getSheetId()

      try {
        workbook.setEditable(false)
        await workbook.getWorkbookPermission().setReadOnly()
      } finally {
        if (previous) univerAPI.disposeUnit(previous)
        if (savedScroll) {
          requestAnimationFrame(() => setScrollState(savedScroll))
        }
        scrollReleaseTimer = setTimeout(() => { suppressScroll = false }, 80)
        selectionReleaseTimer = setTimeout(() => { suppressSelection = false }, 80)
      }
    },
    getScrollState: readScrollState,
    setScrollState,
    focusCell,
    onScroll(handler) {
      scrollListeners.add(handler)
      return () => scrollListeners.delete(handler)
    },
    onSelection(handler) {
      selectionListeners.add(handler)
      return () => selectionListeners.delete(handler)
    },
    dispose() {
      clearTimeout(scrollReleaseTimer)
      clearTimeout(selectionReleaseTimer)
      scrollListeners.clear()
      selectionListeners.clear()
      scrollSub.dispose()
      selectionSub.dispose()
      univer.dispose()
    },
  }
}

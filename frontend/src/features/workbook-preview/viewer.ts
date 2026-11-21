import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core'
import zhCN from '@univerjs/preset-sheets-core/locales/zh-CN'
import { createUniver, LocaleType, type IWorkbookData } from '@univerjs/presets'

import '@univerjs/preset-sheets-core/lib/index.css'

import { autoFitSheetRows, createRenderedGate } from './autoFitRows'

export interface WorkbookViewer {
  /** 显示一张工作表：换掉当前显示的内容，不重新创建整个查看器 */
  show(snapshot: IWorkbookData): Promise<void>
  setZoom(ratio: number): void
  getZoom(): number
  onZoom(handler: (ratio: number) => void): () => void
  dispose(): void
}

/**
 * Viewer-only preset. Permission enforcement also covers keyboard editing.
 * 一次只显示一张工作表（只含这张表的快照），原生底栏已关闭，由页面在同一行统一提供 Sheet 标签与缩放控件。
 */
export function createViewer(container: HTMLElement): WorkbookViewer {
  const { univer, univerAPI } = createUniver({
    locale: LocaleType.ZH_CN,
    locales: { [LocaleType.ZH_CN]: zhCN },
    presets: [UniverSheetsCorePreset({
      container,
      header: false,
      toolbar: false,
      contextMenu: false,
      disableAutoFocus: true,
      formulaBar: true,
      footer: false,
    })],
  })
  const renderedGate = createRenderedGate(univerAPI)
  let current: string | undefined
  let disposed = false
  let unitSeq = 0
  let suppressZoom = false
  let zoomTimer: ReturnType<typeof setTimeout> | undefined
  const zoomListeners = new Set<(ratio: number) => void>()

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
    clearTimeout(zoomTimer)
    try {
      sheet.zoom(clamped)
    } finally {
      zoomTimer = setTimeout(() => { suppressZoom = false }, 50)
    }
  }

  function getZoom(): number {
    return univerAPI.getActiveWorkbook()?.getActiveSheet()?.getZoom() ?? 1
  }

  return {
    async show(snapshot) {
      // 先建新的、再销毁旧的：分配唯一 unit id 防止缓存复用快照与未销毁的旧 unit 冲突
      const unitId = `${snapshot.id || 'preview'}-${++unitSeq}`
      const workbook = univerAPI.createWorkbook({
        ...snapshot,
        id: unitId,
      })
      const previous = current
      current = workbook.getId()
      if (previous) univerAPI.disposeUnit(previous)
      await renderedGate.wait()
      if (disposed || current !== unitId) return
      autoFitSheetRows(workbook, snapshot)
      workbook.setEditable(false)
      await workbook.getWorkbookPermission().setReadOnly()
    },
    setZoom,
    getZoom,
    onZoom(handler) {
      zoomListeners.add(handler)
      return () => zoomListeners.delete(handler)
    },
    dispose() {
      disposed = true
      renderedGate.dispose()
      clearTimeout(zoomTimer)
      zoomListeners.clear()
      zoomSub.dispose()
      univer.dispose()
    },
  }
}

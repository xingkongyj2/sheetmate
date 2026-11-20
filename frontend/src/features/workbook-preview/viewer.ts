import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core'
import zhCN from '@univerjs/preset-sheets-core/locales/zh-CN'
import { createUniver, LocaleType, type IWorkbookData } from '@univerjs/presets'

import '@univerjs/preset-sheets-core/lib/index.css'

export interface WorkbookViewer {
  /** 显示一张工作表：换掉当前显示的内容，不重新创建整个查看器 */
  show(snapshot: IWorkbookData): Promise<void>
  dispose(): void
}

/**
 * Viewer-only preset. Permission enforcement also covers keyboard editing.
 * 一次只显示一张工作表（只含这张表的快照），所以不用 Univer 自带的工作表标签，由页面自己提供。
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
      footer: { sheetBar: false, statisticBar: true, menus: false, zoomSlider: true },
    })],
  })
  let current: string | undefined
  return {
    async show(snapshot) {
      // 先建新的、再销毁旧的：任何时候界面上都有一个工作簿，不会闪一下空白
      const workbook = univerAPI.createWorkbook(snapshot)
      const previous = current
      current = workbook.getId()
      try {
        workbook.setEditable(false)
        await workbook.getWorkbookPermission().setReadOnly()
      } finally {
        if (previous) univerAPI.disposeUnit(previous)
      }
    },
    dispose: () => univer.dispose(),
  }
}

import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core'
import zhCN from '@univerjs/preset-sheets-core/locales/zh-CN'
import { createUniver, LocaleType, type IWorkbookData } from '@univerjs/presets'

import '@univerjs/preset-sheets-core/lib/index.css'

/** Viewer-only preset. Permission enforcement also covers keyboard editing. */
export async function mountWorkbook(container: HTMLElement, snapshot: IWorkbookData) {
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
      footer: { sheetBar: true, statisticBar: true, menus: false, zoomSlider: true, addSheetButtonConfig: { show: false } },
    })],
  })
  try {
    const workbook = univerAPI.createWorkbook(snapshot)
    workbook.setEditable(false)
    await workbook.getWorkbookPermission().setReadOnly()
    return { dispose: () => univer.dispose() }
  } catch (error) {
    univer.dispose()
    throw error
  }
}

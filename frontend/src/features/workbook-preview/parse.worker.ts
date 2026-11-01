import { parseCsvWorkbook } from './csv'
import { parseWorkbook } from './excel'

// Parsing runs outside the UI thread. No file content leaves the browser.
self.onmessage = async (event: MessageEvent<{ buffer: ArrayBuffer; filename: string }>) => {
  try {
    const { buffer, filename } = event.data
    const result = /\.csv$/i.test(filename)
      ? parseCsvWorkbook(buffer, filename)
      : await parseWorkbook(buffer, filename)
    self.postMessage({ ok: true, result })
  } catch (error) {
    self.postMessage({ ok: false, error: error instanceof Error ? error.message : '文件解析失败' })
  }
}

import type { ParsedWorkbook, SheetMeta } from './excel'

/** 在 Worker 里打开的一个文件：目录马上有，工作表内容按需读取。 */
export interface PreviewSession {
  sheets: SheetMeta[]
  /** 文件保存时停留的工作表（在 sheets 里的位置） */
  active: number
  /** 整个文件层面的提示 */
  warnings: string[]
  /** 读取并转换一张工作表（返回的快照里只有这一张表） */
  loadSheet(index: number): Promise<ParsedWorkbook>
  dispose(): void
}

interface Waiting { resolve(value: Record<string, unknown>): void; reject(error: Error): void; timer: ReturnType<typeof setTimeout> }

const OPEN_TIMEOUT = 90_000
const SHEET_TIMEOUT = 90_000

export function openInWorker(buffer: ArrayBuffer, filename: string, signal: AbortSignal): Promise<PreviewSession> {
  const worker = new Worker(new URL('./parse.worker.ts', import.meta.url), { type: 'module' })
  const waiting = new Map<number, Waiting>()
  let closed = false
  let seq = 0

  function dispose(reason: Error = new Error('预览已关闭，请重新加载。')) {
    if (closed) return
    closed = true
    signal.removeEventListener('abort', abort)
    worker.terminate()
    for (const item of waiting.values()) { clearTimeout(item.timer); item.reject(reason) }
    waiting.clear()
  }
  function abort() { dispose(new DOMException('已取消文件预览', 'AbortError')) }

  function request(message: Record<string, unknown>, transfer: Transferable[], timeout: number, timeoutText: string): Promise<Record<string, unknown>> {
    return new Promise((resolve, reject) => {
      if (closed) return reject(new Error('预览已中断，请重新加载。'))
      const id = ++seq
      // 超时就整个终止：Worker 是单线程，卡住的解析没法单独取消
      const timer = setTimeout(() => dispose(new Error(timeoutText)), timeout)
      waiting.set(id, { resolve, reject, timer })
      worker.postMessage({ ...message, id }, transfer)
    })
  }

  worker.onmessage = (event: MessageEvent<{ id: number; ok: boolean; error?: string } & Record<string, unknown>>) => {
    const item = waiting.get(event.data.id)
    if (!item) return
    waiting.delete(event.data.id)
    clearTimeout(item.timer)
    if (event.data.ok) item.resolve(event.data)
    else item.reject(new Error(event.data.error || '文件解析失败'))
  }
  worker.onerror = () => dispose(new Error('无法加载文件预览，请刷新页面后重试。'))

  signal.addEventListener('abort', abort, { once: true })
  if (signal.aborted) {
    abort()
    return Promise.reject(new DOMException('已取消文件预览', 'AbortError'))
  }
  return request({ type: 'open', buffer, filename }, [buffer], OPEN_TIMEOUT, '文件解析时间过长，请下载后使用 Excel 查看。').then((data) => {
    const opened = data.opened as { sheets: SheetMeta[]; active: number; warnings: string[] }
    return {
      ...opened,
      loadSheet: async (index: number) => (await request({ type: 'sheet', index }, [], SHEET_TIMEOUT, '这张工作表解析时间过长，请改用分页预览或下载后使用 Excel 查看。')).result as ParsedWorkbook,
      dispose: () => dispose(),
    }
  }, (error: Error) => {
    dispose()
    throw error
  })
}

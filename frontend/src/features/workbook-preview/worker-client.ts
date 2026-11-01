import type { ParsedWorkbook } from './excel'

export function parseInWorker(buffer: ArrayBuffer, filename: string, signal: AbortSignal): Promise<ParsedWorkbook> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./parse.worker.ts', import.meta.url), { type: 'module' })
    const dispose = () => {
      clearTimeout(timeout)
      signal.removeEventListener('abort', abort)
      worker.terminate()
    }
    const abort = () => {
      dispose()
      reject(new DOMException('已取消文件预览', 'AbortError'))
    }
    const timeout = setTimeout(() => {
      dispose()
      reject(new Error('文件解析时间过长，请下载后使用 Excel 查看。'))
    }, 90_000)
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) return abort()
    worker.onmessage = (event: MessageEvent<{ ok: boolean; result?: ParsedWorkbook; error?: string }>) => {
      dispose()
      if (event.data.ok && event.data.result) resolve(event.data.result)
      else reject(new Error(event.data.error || '文件解析失败'))
    }
    worker.onerror = () => {
      dispose()
      reject(new Error('无法加载文件预览，请刷新页面后重试。'))
    }
    worker.postMessage({ buffer, filename }, [buffer])
  })
}

import type { ParsedWorkbook, SheetMeta } from '../workbook-preview/excel'
import type { AlignMode, DiffOptions, SheetDiffResult, SheetDiffStats } from './types'

export type CompareSide = 'left' | 'right'

export interface OpenedWorkbookMeta {
  sheets: SheetMeta[]
  active: number
  warnings: string[]
}

export interface DiffSheetPayload {
  result: SheetDiffResult
  leftWarnings: string[]
  rightWarnings: string[]
}

export interface CompareWorkerClient {
  open(side: CompareSide, buffer: ArrayBuffer, filename: string): Promise<OpenedWorkbookMeta>
  clear(side: CompareSide): Promise<void>
  swap(): Promise<void>
  loadSingle(side: CompareSide, index: number): Promise<ParsedWorkbook>
  diffSheet(
    leftIndex: number | null,
    rightIndex: number | null,
    options: DiffOptions,
    sheetTitle: string,
  ): Promise<DiffSheetPayload>
  diffSummary(
    leftIndex: number | null,
    rightIndex: number | null,
    alignMode: AlignMode,
  ): Promise<SheetDiffStats>
  dispose(): void
}

interface Waiting {
  resolve(value: Record<string, unknown>): void
  reject(error: Error): void
  timer: ReturnType<typeof setTimeout>
}

const WORKER_TIMEOUT = 90_000

export function createCompareWorker(): CompareWorkerClient {
  const worker = new Worker(new URL('./compare.worker.ts', import.meta.url), { type: 'module' })
  const waiting = new Map<number, Waiting>()
  let closed = false
  let seq = 0

  function dispose(reason: Error = new Error('比对任务已关闭。')) {
    if (closed) return
    closed = true
    worker.terminate()
    for (const item of waiting.values()) {
      clearTimeout(item.timer)
      item.reject(reason)
    }
    waiting.clear()
  }

  function request(
    message: Record<string, unknown>,
    transfer: Transferable[] = [],
    timeoutText = '表格比对耗时过长，请稍后重试。',
  ): Promise<Record<string, unknown>> {
    return new Promise((resolve, reject) => {
      if (closed) return reject(new Error('比对连接已断开，请刷新页面重试。'))
      const id = ++seq
      const timer = setTimeout(() => {
        const pending = waiting.get(id)
        if (!pending) return
        waiting.delete(id)
        pending.reject(new Error(timeoutText))
      }, WORKER_TIMEOUT)
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
    else item.reject(new Error(event.data.error || '表格比对失败'))
  }

  worker.onerror = () => {
    dispose(new Error('无法启动表格比对工作线程，请刷新页面后重试。'))
  }

  return {
    async open(side, buffer, filename) {
      const res = await request(
        { type: 'open', side, buffer, filename },
        [buffer],
        '文件解析耗时过长，请检查文件体积后重试。',
      )
      return res.opened as OpenedWorkbookMeta
    },
    async clear(side) {
      await request({ type: 'clear', side })
    },
    async swap() {
      await request({ type: 'swap' })
    },
    async loadSingle(side, index) {
      const res = await request({ type: 'loadSingle', side, index }, [], '工作表读取超时，请重试。')
      return res.parsed as ParsedWorkbook
    },
    async diffSheet(leftIndex, rightIndex, options, sheetTitle) {
      const res = await request(
        { type: 'diffSheet', leftIndex, rightIndex, options, sheetTitle },
        [],
        '工作表比对耗时过长，请尝试切换为「按坐标对齐」或缩小文件范围。',
      )
      return {
        result: res.result as SheetDiffResult,
        leftWarnings: (res.leftWarnings as string[]) ?? [],
        rightWarnings: (res.rightWarnings as string[]) ?? [],
      }
    },
    async diffSummary(leftIndex, rightIndex, alignMode) {
      const res = await request({ type: 'diffSummary', leftIndex, rightIndex, alignMode })
      return res.stats as SheetDiffStats
    },
    dispose: () => dispose(),
  }
}

/** Add column resizing to sanitized Markdown tables without replacing their rendered cells. */
const MIN_COLUMN_WIDTH = 88

/** Move a boundary between columns; only exhausted neighboring space grows the table. */
export function resizeMarkdownColumnWidths(initial: number[], index: number, delta: number): number[] {
  const widths = [...initial]
  const target = Math.max(MIN_COLUMN_WIDTH, initial[index]! + delta)
  const change = target - initial[index]!
  const neighbor = index + 1 < widths.length ? index + 1 : index - 1
  widths[index] = target
  if (neighbor >= 0) widths[neighbor] = Math.max(MIN_COLUMN_WIDTH, initial[neighbor]! - change)
  return widths
}

interface TableBinding {
  table: HTMLTableElement
  wrapper: HTMLElement
  headers: HTMLTableCellElement[]
  handles: HTMLElement[]
  key: string
  widths?: number[]
  columns?: HTMLTableColElement[]
  colgroup?: HTMLTableColElement
  originalWidth: string
  originalLayout: string
  dispose: (() => void)[]
}

export function createMarkdownTableController() {
  const savedWidths = new Map<string, number[]>()
  const bindings = new Map<HTMLTableElement, TableBinding>()
  let finishDrag: (() => void) | undefined

  function updateHandleValues(binding: TableBinding) {
    binding.handles.forEach((handle, index) => {
      const width = Math.round(binding.headers[index]!.getBoundingClientRect().width)
      handle.setAttribute('aria-valuemax', String(Math.max(binding.wrapper.clientWidth, width, MIN_COLUMN_WIDTH) * 2))
      handle.setAttribute('aria-valuenow', String(width))
      handle.setAttribute('aria-valuetext', `${width} 像素`)
    })
  }

  function applyWidths(binding: TableBinding) {
    if (!binding.widths) {
      updateHandleValues(binding)
      return
    }
    const { table, widths } = binding
    const total = widths.reduce((sum, width) => sum + width, 0)
    const available = binding.wrapper.clientWidth
    if (total < available) {
      widths[widths.length - 1]! += available - total
      savedWidths.set(binding.key, [...widths])
    }
    if (!binding.colgroup) {
      const group = table.ownerDocument.createElement('colgroup')
      group.dataset.markdownColumns = ''
      binding.columns = widths.map(() => {
        const column = table.ownerDocument.createElement('col')
        group.append(column)
        return column
      })
      table.insertBefore(group, table.tHead ?? table.tBodies[0] ?? null)
      binding.colgroup = group
    }
    widths.forEach((width, index) => {
      binding.columns![index]!.style.width = `${width}px`
    })
    table.style.tableLayout = 'fixed'
    table.style.width = `${Math.max(binding.wrapper.clientWidth, widths.reduce((sum, width) => sum + width, 0))}px`
    updateHandleValues(binding)
  }

  function rememberWidths(binding: TableBinding, widths: number[]) {
    binding.widths = widths.map((width) => Math.max(MIN_COLUMN_WIDTH, Math.round(width)))
    savedWidths.set(binding.key, [...binding.widths])
    applyWidths(binding)
  }

  function measuredWidths(binding: TableBinding): number[] {
    return binding.headers.map((header) => Math.max(MIN_COLUMN_WIDTH, header.getBoundingClientRect().width))
  }

  function reset(binding: TableBinding) {
    finishDrag?.()
    savedWidths.delete(binding.key)
    binding.widths = undefined
    binding.colgroup?.remove()
    binding.colgroup = undefined
    binding.columns = undefined
    binding.table.style.width = binding.originalWidth
    binding.table.style.tableLayout = binding.originalLayout
    updateHandleValues(binding)
  }

  function bindHandle(binding: TableBinding, header: HTMLTableCellElement, index: number) {
    const document = binding.table.ownerDocument
    const handle = document.createElement('span')
    const label = header.textContent?.trim() || `第 ${index + 1}`
    handle.className = 'markdown-column-resize'
    handle.tabIndex = 0
    handle.setAttribute('role', 'separator')
    handle.setAttribute('aria-orientation', 'vertical')
    handle.setAttribute('aria-label', `调整“${label}”列宽`)
    handle.setAttribute('aria-valuemin', String(MIN_COLUMN_WIDTH))
    handle.setAttribute('aria-keyshortcuts', 'ArrowLeft ArrowRight Home')
    handle.title = '拖动调整列宽，双击恢复自适应'

    const pointerdown = (event: PointerEvent) => {
      if (event.button !== 0) return
      const view = document.defaultView
      if (!view) return
      event.preventDefault()
      event.stopPropagation()
      finishDrag?.()
      const initial = measuredWidths(binding)
      const startX = event.clientX
      const pointerId = event.pointerId
      const previousSelection = document.body.style.userSelect
      const previousCursor = document.documentElement.style.cursor
      document.body.style.userSelect = 'none'
      document.documentElement.style.cursor = 'col-resize'
      binding.table.classList.add('is-resizing')
      handle.dataset.resizing = ''

      const pointermove = (move: PointerEvent) => {
        if (move.pointerId !== pointerId) return
        move.preventDefault()
        rememberWidths(binding, resizeMarkdownColumnWidths(initial, index, move.clientX - startX))
      }
      const pointerend = (end: PointerEvent) => {
        if (end.pointerId === pointerId) finish()
      }
      const finish = () => {
        view.removeEventListener('pointermove', pointermove)
        view.removeEventListener('pointerup', pointerend)
        view.removeEventListener('pointercancel', pointerend)
        handle.removeEventListener('lostpointercapture', pointerend)
        binding.table.classList.remove('is-resizing')
        delete handle.dataset.resizing
        document.body.style.userSelect = previousSelection
        document.documentElement.style.cursor = previousCursor
        finishDrag = undefined
        if (handle.hasPointerCapture?.(pointerId)) handle.releasePointerCapture(pointerId)
      }
      finishDrag = finish
      view.addEventListener('pointermove', pointermove, { passive: false })
      view.addEventListener('pointerup', pointerend)
      view.addEventListener('pointercancel', pointerend)
      handle.addEventListener('lostpointercapture', pointerend)
      try { handle.setPointerCapture(pointerId) } catch { /* Window listeners also cover capture-less hosts. */ }
    }
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Home') {
        event.preventDefault()
        reset(binding)
      } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault()
        rememberWidths(binding, resizeMarkdownColumnWidths(binding.widths ?? measuredWidths(binding), index,
          (event.key === 'ArrowRight' ? 1 : -1) * (event.shiftKey ? 40 : 10)))
      }
    }
    const dblclick = (event: MouseEvent) => {
      event.preventDefault()
      event.stopPropagation()
      reset(binding)
    }
    handle.addEventListener('pointerdown', pointerdown)
    handle.addEventListener('keydown', keydown)
    handle.addEventListener('dblclick', dblclick)
    binding.dispose.push(() => {
      handle.removeEventListener('pointerdown', pointerdown)
      handle.removeEventListener('keydown', keydown)
      handle.removeEventListener('dblclick', dblclick)
      handle.remove()
    })
    header.append(handle)
    binding.handles.push(handle)
  }

  function unbind(binding: TableBinding) {
    if (binding.table.classList.contains('is-resizing')) finishDrag?.()
    binding.dispose.forEach((dispose) => dispose())
    binding.colgroup?.remove()
    binding.table.style.width = binding.originalWidth
    binding.table.style.tableLayout = binding.originalLayout
    bindings.delete(binding.table)
  }

  function refresh(root: HTMLElement | null) {
    const tables = root ? [...root.querySelectorAll<HTMLTableElement>('table')] : []
    const current = new Set(tables)
    for (const binding of bindings.values()) {
      if (!current.has(binding.table)) unbind(binding)
    }
    const occurrences = new Map<string, number>()
    const activeKeys = new Set<string>()
    for (const table of tables) {
      const existing = bindings.get(table)
      if (existing) {
        activeKeys.add(existing.key)
        const separator = existing.key.lastIndexOf(':')
        const signature = existing.key.slice(0, separator)
        const nextOccurrence = Number(existing.key.slice(separator + 1)) + 1
        occurrences.set(signature, Math.max(occurrences.get(signature) ?? 0, nextOccurrence))
        continue
      }
      const row = table.tHead?.rows[0] ?? table.rows[0]
      const headers = row ? [...row.cells].filter((cell) => cell.tagName === 'TH') : []
      const signature = JSON.stringify(headers.map((header) => header.textContent?.trim() ?? ''))
      const occurrence = occurrences.get(signature) ?? 0
      occurrences.set(signature, occurrence + 1)
      const key = `${signature}:${occurrence}`
      activeKeys.add(key)
      let wrapper = table.parentElement
      if (!wrapper?.classList.contains('markdown-table-scroll')) {
        wrapper = table.ownerDocument.createElement('div')
        wrapper.className = 'markdown-table-scroll'
        wrapper.tabIndex = 0
        wrapper.setAttribute('role', 'region')
        wrapper.setAttribute('aria-label', '表格，可横向滚动')
        table.before(wrapper)
        wrapper.append(table)
      }
      const binding: TableBinding = {
        table, wrapper, headers, handles: [], key,
        widths: savedWidths.get(key)?.slice(),
        originalWidth: table.style.width,
        originalLayout: table.style.tableLayout,
        dispose: [],
      }
      bindings.set(table, binding)
      // Spanned HTML headers need a different width model; plain Markdown has one header per column.
      if (headers.length && headers.length === row?.cells.length && headers.every((header) => header.colSpan === 1)) {
        headers.forEach((header, index) => bindHandle(binding, header, index))
        applyWidths(binding)
      }
      if (typeof ResizeObserver !== 'undefined') {
        const observer = new ResizeObserver(() => applyWidths(binding))
        observer.observe(wrapper)
        binding.dispose.push(() => observer.disconnect())
      }
    }
    for (const key of savedWidths.keys()) {
      if (!activeKeys.has(key)) savedWidths.delete(key)
    }
  }

  return {
    refresh,
    destroy() {
      finishDrag?.()
      for (const binding of bindings.values()) unbind(binding)
      savedWidths.clear()
    },
  }
}

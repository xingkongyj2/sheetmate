/**
 * 用户消息和输入框里的 Markdown。
 *
 * 文件、工作表引用（标签）不是 Markdown：先换成占位符，整段按 Markdown 渲染，再把占位符换回标签，
 * 这样标签可以出现在标题、列表、加粗等任何位置，渲染结果里也不会丢。
 */
import { parseTableReferences, type FileReference, type TableReference } from './tableReferences.ts'

const OPEN = ''
const CLOSE = ''
const placeholder = /(\d+)/g

export interface MessageChip {
  kind: 'file' | 'table'
  label: string
  title: string
}

/** 有标题、列表、引用、代码块、表格、加粗这类写法才按 Markdown 渲染；普通的一两句话保持原样。 */
export function looksLikeMarkdown(text: string): boolean {
  return /^#{1,6}\s+\S|^\s*[-*+]\s+\S|^\s*\d+[.)]\s+\S|^>\s|^```|^\|.+\|\s*$|\*\*[^*\n]+\*\*/m.test(text)
}

export function markdownWithChips(text: string, tables: TableReference[] = [], files: FileReference[] = []): { source: string; chips: MessageChip[] } {
  const chips: MessageChip[] = []
  let source = ''
  for (const segment of parseTableReferences(text, tables, files)) {
    if (segment.type === 'text') {
      source += segment.text
      continue
    }
    chips.push(segment.type === 'file'
      ? { kind: 'file', label: segment.reference.filename, title: segment.reference.filename }
      : { kind: 'table', label: segment.reference.sheet, title: `${segment.reference.filename} / ${segment.reference.sheet}` })
    source += `${OPEN}${chips.length - 1}${CLOSE}`
  }
  return { source, chips }
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!)
}

/** 把渲染后 HTML 里的占位符换回标签。标签文字一律转义，文件名里的 < > 不会变成标签。 */
export function fillChips(html: string, chips: MessageChip[]): string {
  return html.replace(placeholder, (_match, index: string) => {
    const chip = chips[Number(index)]
    if (!chip) return ''
    const colors = chip.kind === 'file' ? 'bg-[#e4eaf2] text-[#42566f]' : 'bg-[#e0ece5] text-[#3f6152]'
    return `<span class="mx-0.5 rounded-md px-1.5 py-0.5 ${colors}" title="${escapeHtml(chip.title)}">${escapeHtml(chip.label)}</span>`
  })
}

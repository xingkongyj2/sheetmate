import DOMPurify from 'dompurify'

import { markdownParser } from './markdownParser'

export function renderMarkdown(source: string): string {
  return DOMPurify.sanitize(markdownParser.parse(source || '', { async: false }))
}

function formattedPayload(markdown: string): { html: string; text: string } {
  const container = document.createElement('div')
  container.innerHTML = renderMarkdown(markdown)
  container.style.cssText = 'font-family:Arial,sans-serif;font-size:15px;line-height:1.7;color:#202124'
  const styles: Record<string, string> = {
    h1: 'font-size:24px;font-weight:700;margin:20px 0 10px',
    h2: 'font-size:20px;font-weight:700;margin:18px 0 8px',
    h3: 'font-size:17px;font-weight:700;margin:16px 0 8px',
    h4: 'font-size:15px;font-weight:700;margin:12px 0 6px',
    p: 'margin:8px 0',
    ul: 'list-style-type:disc;padding-left:24px;margin:8px 0',
    ol: 'list-style-type:decimal;padding-left:24px;margin:8px 0',
    li: 'margin:4px 0',
    table: 'width:100%;border-collapse:collapse;margin:16px 0 20px;font-size:14px;line-height:1.65;border:1px solid #dfe3e8;font-variant-numeric:tabular-nums',
    th: 'border:1px solid #dfe3e8;padding:11px 18px;background:#f7f8fa;color:#52525b;font-size:13px;font-weight:600',
    td: 'border:1px solid #e1e5ea;padding:12px 18px;vertical-align:top',
    blockquote: 'border-left:3px solid #d9dde3;padding-left:12px;margin:12px 0;color:#5e6470',
    pre: 'white-space:pre-wrap;background:#f3f4f6;padding:12px;font-family:monospace',
    code: 'font-family:monospace;background:#f3f4f6',
  }
  for (const [selector, style] of Object.entries(styles)) {
    for (const element of container.querySelectorAll<HTMLElement>(selector)) element.style.cssText = style
  }
  for (const cell of container.querySelectorAll<HTMLElement>('th:not([align]), td:not([align])')) cell.style.textAlign = 'left'
  for (const row of container.querySelectorAll<HTMLElement>('tbody tr:nth-child(even)')) row.style.background = '#fafbfc'
  for (const link of container.querySelectorAll<HTMLAnchorElement>('a[href]')) link.href = link.href
  const host = document.createElement('div')
  host.style.cssText = 'position:fixed;left:-10000px;top:0;width:800px;pointer-events:none'
  host.setAttribute('aria-hidden', 'true')
  host.append(container)
  document.body.append(host)
  try {
    return { html: container.outerHTML, text: container.innerText }
  } finally {
    host.remove()
  }
}

function legacyCopy(text: string, html?: string): void {
  const active = document.activeElement as HTMLElement | null
  const field = document.createElement('textarea')
  field.value = text
  field.style.cssText = 'position:fixed;left:-10000px;top:0'
  field.setAttribute('aria-hidden', 'true')
  document.body.append(field)
  let wrote = false
  const onCopy = (event: ClipboardEvent) => {
    if (!event.clipboardData) return
    event.preventDefault()
    event.clipboardData.setData('text/plain', text)
    if (html) event.clipboardData.setData('text/html', html)
    wrote = true
  }
  document.addEventListener('copy', onCopy)
  try {
    field.select()
    const copied = document.execCommand('copy')
    if (!copied || (html && !wrote)) throw new Error('浏览器未允许复制，请检查剪贴板权限后重试')
  } finally {
    document.removeEventListener('copy', onCopy)
    field.remove()
    active?.focus({ preventScroll: true })
  }
}

export async function copyMarkdown(markdown: string, format: 'formatted' | 'markdown'): Promise<void> {
  const payload = format === 'formatted' ? formattedPayload(markdown) : { text: markdown, html: undefined }
  try {
    if (format === 'formatted') {
      if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') throw new Error('不支持富文本剪贴板')
      await navigator.clipboard.write([new ClipboardItem({
        'text/html': new Blob([payload.html!], { type: 'text/html' }),
        'text/plain': new Blob([payload.text], { type: 'text/plain' }),
      })])
    } else {
      if (!navigator.clipboard?.writeText) throw new Error('不支持文本剪贴板')
      await navigator.clipboard.writeText(markdown)
    }
  } catch {
    legacyCopy(payload.text, payload.html)
  }
}

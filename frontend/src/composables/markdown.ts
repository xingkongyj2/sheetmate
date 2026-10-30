import DOMPurify from 'dompurify'
import { marked } from 'marked'

export function renderMarkdown(source: string): string {
  return DOMPurify.sanitize(marked.parse(source || '', { async: false, gfm: true, breaks: true }) as string)
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
    table: 'border-collapse:collapse;margin:12px 0;font-size:14px',
    th: 'border:1px solid #d9dde3;padding:8px 12px;background:#f3f4f6;font-weight:700',
    td: 'border:1px solid #d9dde3;padding:8px 12px',
    blockquote: 'border-left:3px solid #d9dde3;padding-left:12px;margin:12px 0;color:#5e6470',
    pre: 'white-space:pre-wrap;background:#f3f4f6;padding:12px;font-family:monospace',
    code: 'font-family:monospace;background:#f3f4f6',
  }
  for (const [selector, style] of Object.entries(styles)) {
    for (const element of container.querySelectorAll<HTMLElement>(selector)) element.style.cssText = style
  }
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

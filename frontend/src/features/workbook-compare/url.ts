import type { CompareSource } from './types.ts'

export type ParseCompareInputResult =
  | { ok: true; source: CompareSource }
  | { ok: false; error: string }

const FILE_ID_PATTERN = /^f_[a-zA-Z0-9_-]{4,64}$/

/**
 * 解析用户在比对页输入的另一个表格链接。
 * 支持：
 * 1. 本站表格浏览页链接：https://.../preview/<fileId> 或 /preview/<fileId>
 * 2. 本站本地文件浏览页链接：/preview/local?blob=blob:...&name=...
 * 3. 本站文件接口链接：/api/files/<fileId>(/download|/content|/preview)?
 * 4. 直接粘贴的 fileId（如 f_1234567890）
 */
export function parseCompareTargetInput(raw: string, origin = typeof location !== 'undefined' ? location.origin : 'http://localhost'): ParseCompareInputResult {
  const input = raw.trim()
  if (!input) return { ok: false, error: '请输入另一个表格的浏览页链接。' }

  if (FILE_ID_PATTERN.test(input)) {
    return { ok: true, source: { kind: 'remote', fileId: input } }
  }

  let parsed: URL
  try {
    const hasScheme = /^[a-z][a-z\d+\-.]*:/i.test(input)
    if (hasScheme) {
      parsed = new URL(input)
    } else if (input.startsWith('/')) {
      parsed = new URL(input, origin)
    } else if (/^(?:preview|api)\//i.test(input)) {
      parsed = new URL(`/${input}`, origin)
    } else {
      return { ok: false, error: '无法识别这个链接，请复制另一个表格浏览页的完整地址（如 /preview/...）。' }
    }
  } catch {
    return { ok: false, error: '链接格式不正确，请复制表格浏览页的完整地址。' }
  }

  if (!/^https?:$/i.test(parsed.protocol)) {
    return { ok: false, error: '不支持这个链接协议，请粘贴本站表格浏览页的地址。' }
  }

  const sameOrigin = !origin || parsed.origin === origin
  const pathname = parsed.pathname.replace(/\/+$/, '')

  if (/^\/compare$/i.test(pathname)) {
    return { ok: false, error: '这是比对页的地址，请粘贴单个表格浏览页的地址（/preview/...）。' }
  }

  const previewMatch = /^\/preview\/([^/]+)$/i.exec(pathname)
  if (previewMatch) {
    const id = decodeURIComponent(previewMatch[1]!)
    if (id === 'local') {
      const blobUrl = parsed.searchParams.get('blob') ?? ''
      const name = parsed.searchParams.get('name') || '本地文件'
      if (!sameOrigin || !blobUrl.startsWith(`blob:${origin}/`)) {
        return { ok: false, error: '这个本地文件的预览链接已失效或来自其他站点，请点击「选择本地文件」重新打开。' }
      }
      return { ok: true, source: { kind: 'local', blobUrl, name } }
    }
    if (!sameOrigin) {
      return { ok: false, error: '暂不支持跨站链接，请粘贴当前站点下另一个表格的浏览页地址。' }
    }
    return { ok: true, source: { kind: 'remote', fileId: id } }
  }

  const apiMatch = /^\/api\/files\/([^/]+)(?:\/(?:download|content|preview))?$/i.exec(pathname)
  if (apiMatch) {
    if (!sameOrigin) {
      return { ok: false, error: '暂不支持跨站链接，请粘贴当前站点下另一个表格的地址。' }
    }
    return { ok: true, source: { kind: 'remote', fileId: decodeURIComponent(apiMatch[1]!) } }
  }

  if (!sameOrigin) {
    return { ok: false, error: '暂不支持直接读取外部网站链接（受浏览器安全限制），请粘贴本站表格浏览页地址，或下载后点击「选择本地文件」。' }
  }

  return { ok: false, error: '这个链接不是表格浏览页地址，请打开另一个表格后复制浏览器地址栏中的链接。' }
}

/** 构造 /compare 路由的 query 参数（保留可刷新的链接状态与所属会话） */
export function buildCompareQuery(
  left?: CompareSource | null,
  right?: CompareSource | null,
  conversationId?: string | null,
): Record<string, string> {
  const query: Record<string, string> = {}
  if (conversationId) query.conversation = conversationId
  if (left?.kind === 'remote') {
    query.left = left.fileId
  } else if (left?.kind === 'local') {
    query.left = 'local'
    query.leftBlob = left.blobUrl
    query.leftName = left.name
  }
  if (right?.kind === 'remote') {
    query.right = right.fileId
  } else if (right?.kind === 'local') {
    query.right = 'local'
    query.rightBlob = right.blobUrl
    query.rightName = right.name
  }
  return query
}

/** 从路由 query 恢复一侧的 CompareSource */
export function sourceFromQuery(
  query: Record<string, unknown>,
  side: 'left' | 'right',
  origin = typeof location !== 'undefined' ? location.origin : 'http://localhost',
): CompareSource | null {
  const raw = typeof query[side] === 'string' ? query[side].trim() : ''
  if (!raw) return null
  if (raw === 'local') {
    const blob = typeof query[`${side}Blob`] === 'string' ? query[`${side}Blob`] as string : ''
    const name = typeof query[`${side}Name`] === 'string' && (query[`${side}Name`] as string).trim()
      ? (query[`${side}Name`] as string).trim()
      : '本地文件'
    if (!blob.startsWith(`blob:${origin}/`)) return null
    return { kind: 'local', blobUrl: blob, name }
  }
  return { kind: 'remote', fileId: raw }
}

/** 判断两个来源是否指向同一份文件 */
export function isSameCompareSource(a: CompareSource | null, b: CompareSource | null): boolean {
  if (!a || !b || a.kind !== b.kind) return false
  if (a.kind === 'remote' && b.kind === 'remote') return a.fileId === b.fileId
  if (a.kind === 'local' && b.kind === 'local') return a.blobUrl === b.blobUrl
  if (a.kind === 'file' && b.kind === 'file') return a.file === b.file
  return false
}

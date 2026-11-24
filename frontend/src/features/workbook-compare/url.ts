import type { CompareSource } from './types.ts'

export type ParseCompareInputResult =
  | { ok: true; source: CompareSource }
  | { ok: false; error: string }

const FILE_ID_PATTERN = /^f_[a-zA-Z0-9_-]{4,64}$/

/**
 * 解析用户在比对页输入的表格链接。
 * 1. 本站表格浏览页 / 文件接口链接（/preview/<fileId>、/api/files/<fileId>...）或直接粘贴的 fileId → 当前系统里的文件；
 * 2. 本站本地文件浏览页链接：/preview/local?blob=blob:...&name=...
 * 3. 其他任意 http(s) 链接（如 .../订单.xlsx）→ 由后端代取。
 */
export function parseCompareTargetInput(raw: string, origin = typeof location !== 'undefined' ? location.origin : 'http://localhost'): ParseCompareInputResult {
  const input = raw.trim()
  if (!input) return { ok: false, error: '请输入表格链接。' }

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
    } else if (/^[^\s/]+\.[^\s/]+(?:[/?#]|$)/.test(input)) {
      parsed = new URL(`https://${input}`)          // 省略协议的域名，如 example.com/a.xlsx
    } else {
      return { ok: false, error: '无法识别这个链接，请粘贴以 http(s):// 开头的完整表格地址。' }
    }
  } catch {
    return { ok: false, error: '链接格式不正确，请粘贴完整的表格地址。' }
  }

  if (!/^https?:$/i.test(parsed.protocol)) {
    return { ok: false, error: '只支持 http / https 链接。' }
  }

  const sameOrigin = !origin || parsed.origin === origin
  const pathname = parsed.pathname.replace(/\/+$/, '')

  if (sameOrigin && /^\/compare$/i.test(pathname)) {
    return { ok: false, error: '这是比对页的地址，请粘贴单个表格的地址。' }
  }

  const previewMatch = sameOrigin ? /^\/preview\/([^/]+)$/i.exec(pathname) : null
  if (previewMatch) {
    const id = decodeURIComponent(previewMatch[1]!)
    if (id === 'local') {
      const blobUrl = parsed.searchParams.get('blob') ?? ''
      const name = parsed.searchParams.get('name') || '本地文件'
      if (!blobUrl.startsWith(`blob:${origin}/`)) {
        return { ok: false, error: '这个本地文件的预览链接已失效，请重新打开。' }
      }
      return { ok: true, source: { kind: 'local', blobUrl, name } }
    }
    return { ok: true, source: { kind: 'remote', fileId: id } }
  }

  const apiMatch = sameOrigin ? /^\/api\/files\/([^/]+)(?:\/(?:download|content|preview))?$/i.exec(pathname) : null
  if (apiMatch) {
    return { ok: true, source: { kind: 'remote', fileId: decodeURIComponent(apiMatch[1]!) } }
  }

  return { ok: true, source: { kind: 'url', url: parsed.toString() } }
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
  } else if (left?.kind === 'url') {
    query.left = 'url'
    query.leftUrl = left.url
  } else if (left?.kind === 'local') {
    query.left = 'local'
    query.leftBlob = left.blobUrl
    query.leftName = left.name
  }
  if (right?.kind === 'remote') {
    query.right = right.fileId
  } else if (right?.kind === 'url') {
    query.right = 'url'
    query.rightUrl = right.url
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
  if (raw === 'url') {
    const url = typeof query[`${side}Url`] === 'string' ? (query[`${side}Url`] as string).trim() : ''
    return /^https?:\/\//i.test(url) ? { kind: 'url', url } : null
  }

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
  if (a.kind === 'url' && b.kind === 'url') return a.url === b.url
  if (a.kind === 'local' && b.kind === 'local') return a.blobUrl === b.blobUrl
  if (a.kind === 'file' && b.kind === 'file') return a.file === b.file
  return false
}

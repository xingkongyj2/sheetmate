import { markdownParser } from './markdownParser'

export interface MarkdownBlock {
  key: number
  raw: string
  html: string
}

/** Reuse completed Markdown blocks so each new chunk only redraws the unfinished block. */
export function markdownBlocks(source: string, previous: MarkdownBlock[] = []): MarkdownBlock[] {
  const tokens = markdownParser.lexer(source)
  const cached = new Map(previous.map((block) => [block.key, block]))
  let offset = 0
  return tokens.flatMap((token) => {
    const key = offset
    offset += token.raw.length
    const existing = cached.get(key)
    if (existing?.raw === token.raw) return [existing]
    const html = markdownParser.parser(Object.assign([token], { links: tokens.links }))
    return html.trim() ? [{ key, raw: token.raw, html }] : []
  })
}

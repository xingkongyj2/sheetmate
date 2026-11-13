/**
 * 把单行 SQL 排成容易读的样子：主要子句换行、选择列逐行、子查询缩进。
 * 只改空白，不改任何字符；排完后去掉空白与原文不一致就原样返回。
 */
const TOKEN = /'(?:''|[^'])*'|"(?:""|[^"])*"|--[^\n]*|\/\*[\s\S]*?\*\/|[(),;]|[^\s(),;'"]+/g
const CLAUSES = new Set(['SELECT', 'FROM', 'WHERE', 'HAVING', 'LIMIT', 'WITH', 'QUALIFY', 'WINDOW', 'VALUES'])
const JOIN_PREFIX = new Set(['LEFT', 'RIGHT', 'INNER', 'FULL', 'CROSS', 'NATURAL'])
const KEYWORDS = new Set(['IN', 'ON', 'AND', 'OR', 'NOT', 'AS', 'FROM', 'JOIN', 'WHERE', 'SELECT', 'WHEN', 'THEN', 'ELSE', 'BY', 'USING',
  'OVER', 'EXISTS', 'UNION', 'ALL', 'ANY', 'HAVING', 'WITH', 'CASE', 'END', 'IS', 'LIKE', 'BETWEEN', 'DISTINCT', 'LIMIT', 'OFFSET'])

const squash = (text: string) => text.replace(/\s+/g, '')

export function formatSql(source: string): string {
  const sql = source.trim()
  if (!sql) return source
  const tokens = sql.match(TOKEN) ?? []
  const lines: string[] = []
  let line = ''
  let indent = 0
  let previous = ''
  /** 每个括号一项：block=子查询（独占缩进），inline=函数参数等（不换行） */
  const stack: { block: boolean; indent: number }[] = []
  const directly = () => !stack.length || stack[stack.length - 1]!.block
  let clause = ''

  const newline = (level: number) => {
    if (line.trim()) lines.push(line.replace(/\s+$/, ''))
    line = '  '.repeat(Math.max(0, level))
    previous = ''
  }
  const put = (token: string) => {
    const bare = line.trim() === ''
    const upper = previous.toUpperCase()
    const glue = bare || previous === '(' || token === ')' || token === ',' || token === ';' || previous.endsWith('.') || token.startsWith('.')
      || token.startsWith('::') || (token === '(' && /^[A-Za-z_][\w]*$/.test(previous) && !KEYWORDS.has(upper))
    line += (glue ? '' : ' ') + token
    previous = token
  }

  for (let i = 0; i < tokens.length; i++) {
    let token = tokens[i]!
    const upper = token.toUpperCase()
    if (token === '(') {
      const next = (tokens[i + 1] ?? '').toUpperCase()
      const block = next === 'SELECT' || next === 'WITH'
      stack.push({ block, indent })
      put('(')
      if (block) { indent += 1; newline(indent); clause = '' }
      continue
    }
    if (token === ')') {
      const frame = stack.pop()
      if (frame?.block) { indent = frame.indent; newline(indent) }
      put(')')
      continue
    }
    if (token === ',') {
      put(',')
      if (directly() && (clause === 'SELECT' || clause === 'WITH')) newline(clause === 'WITH' ? indent : indent + 1)
      continue
    }
    if (token === ';') { put(';'); newline(indent); continue }
    if (token.startsWith('--')) { put(token); newline(indent); continue }

    if (directly()) {
      // 连词：LEFT JOIN、GROUP BY、ORDER BY、UNION ALL 当作一个关键字
      let phrase = ''
      if (JOIN_PREFIX.has(upper)) {
        let j = i + 1
        if ((tokens[j] ?? '').toUpperCase() === 'OUTER') j++
        if ((tokens[j] ?? '').toUpperCase() === 'JOIN') { phrase = tokens.slice(i, j + 1).join(' '); i = j }
      } else if (upper === 'JOIN') phrase = token
      else if ((upper === 'GROUP' || upper === 'ORDER' || upper === 'PARTITION') && (tokens[i + 1] ?? '').toUpperCase() === 'BY' && upper !== 'PARTITION') { phrase = `${token} ${tokens[i + 1]}`; i++ }
      else if (upper === 'UNION' || upper === 'INTERSECT' || upper === 'EXCEPT') {
        phrase = (tokens[i + 1] ?? '').toUpperCase() === 'ALL' ? `${token} ${tokens[i + 1]}` : token
        if (phrase !== token) i++
      }
      if (phrase) {
        newline(indent)
        put(phrase)
        clause = phrase.toUpperCase().includes('JOIN') ? 'FROM' : phrase.toUpperCase().split(' ')[0]!
        if (clause === 'UNION' || clause === 'INTERSECT' || clause === 'EXCEPT') { clause = ''; newline(indent) }
        continue
      }
      if (CLAUSES.has(upper) && !(upper === 'WITH' && lines.length && clause !== '')) {
        newline(indent)
        put(token)
        clause = upper
        if (upper === 'SELECT') {
          if ((tokens[i + 1] ?? '').toUpperCase() === 'DISTINCT') { put(tokens[i + 1]!); i++ }
          newline(indent + 1)
        }
        continue
      }
      if (clause === 'WHERE' && (upper === 'AND' || upper === 'OR')) { newline(indent + 1); put(token); continue }
    }
    put(token)
    token = ''
  }
  if (line.trim()) lines.push(line.replace(/\s+$/, ''))
  const formatted = lines.join('\n')
  return squash(formatted) === squash(sql) ? formatted : source
}

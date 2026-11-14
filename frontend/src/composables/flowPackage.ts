/**
 * 流程包：在前端解开，当成一次全新的输入。
 *
 * 解出的输入表格，和用户自己选的文件走同一条上传路径；处理逻辑写进输入框，等同于用户自己打了一段需求。
 * 后端不认识压缩包，也不做任何特殊处理——流程包只是帮用户把「文件 + 需求」一次放进输入框。
 *
 * 只取输入文件和处理逻辑：包里的输出样例、人工结果样例不导入，也就不做目标文件校验。
 */
import JSZip from 'jszip'

const MARKER_FILE = '流程包.json'
const DEFAULT_LOGIC_FILE = '处理逻辑.md'
const FORMAT = 'sheetmate.flow-package'
const MAX_ENTRIES = 64
const MAX_UNPACKED_BYTES = 200 * 1024 * 1024
const REQUEST = '请根据下面的处理逻辑，用我上传的文件建立这个流程。'
const mimeByExtension: Record<string, string> = {
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  xlsm: 'application/vnd.ms-excel.sheet.macroEnabled.12',
  xls: 'application/vnd.ms-excel',
  csv: 'text/csv',
}

export interface UnpackedPackage {
  files: File[]
  text: string
}

export const isZipFile = (file: Pick<File, 'name'>): boolean => /\.zip$/i.test(file.name)

function safeName(path: unknown): string | null {
  if (typeof path !== 'string') return null
  const parts = path.split('/')
  if (path.startsWith('/') || parts.includes('..')) return null
  return parts[parts.length - 1] || null
}

/** 处理逻辑里只留「要做什么」：去掉样例结果相关的行，去掉原流程的实现细节（参考实现）。 */
export function logicText(markdown: string): string {
  const kept: string[] = []
  let skipping = false
  for (const line of markdown.split(/\r?\n/)) {
    if (line.startsWith('## ')) {
      if (line.startsWith('## 参考实现')) break
      skipping = line.startsWith('## 期望结果样例')
    }
    if (skipping || line.startsWith('- 输出/')) continue
    kept.push(line)
  }
  return kept.join('\n').replace(/\n{3,}/g, '\n\n').trim()
}

/**
 * 解开一个流程包。不是流程包（没有标志文件、格式不对、损坏）返回 null；
 * 是流程包但里面没有可用的输入文件，抛出带说明的错误。
 */
export async function unpackFlowPackage(source: Blob | ArrayBuffer): Promise<UnpackedPackage | null> {
  let zip: JSZip
  try {
    zip = await JSZip.loadAsync(source instanceof Blob ? await source.arrayBuffer() : source)
  } catch {
    return null
  }
  const marker = zip.file(MARKER_FILE)
  if (!marker) return null
  let manifest: Record<string, unknown>
  try {
    manifest = JSON.parse(await marker.async('string'))
  } catch {
    return null
  }
  if (!manifest || typeof manifest !== 'object' || manifest.format !== FORMAT) return null
  if (Object.keys(zip.files).length > MAX_ENTRIES) throw new Error('流程包里的文件太多。')

  let total = 0
  const read = async (path: unknown): Promise<Uint8Array | null> => {
    if (typeof path !== 'string' || !safeName(path)) return null
    const entry = zip.file(path)
    if (!entry) return null
    const declared = (entry as unknown as { _data?: { uncompressedSize?: number } })._data?.uncompressedSize
    if (typeof declared === 'number' && total + declared > MAX_UNPACKED_BYTES) throw new Error('流程包太大。')
    const bytes = await entry.async('uint8array')
    total += bytes.length
    if (total > MAX_UNPACKED_BYTES) throw new Error('流程包太大。')
    return bytes
  }

  const files: File[] = []
  const inputs = Array.isArray(manifest.inputs) ? manifest.inputs : []
  for (const item of inputs) {
    if (!item || typeof item !== 'object') continue
    const { file, kind } = item as { file?: unknown; kind?: unknown }
    const name = safeName(file)
    const extension = name?.match(/\.(xlsx|xlsm|xls|csv)$/i)?.[1]?.toLowerCase()
    if (!name || !extension || kind === 'expected') continue  // 人工结果样例不导入
    const bytes = await read(file)
    if (bytes) files.push(new File([bytes as BlobPart], name, { type: mimeByExtension[extension] }))
  }
  if (!files.length) throw new Error('流程包里没有可用的输入文件。')

  const logicPath = typeof manifest.logic === 'string' ? manifest.logic : DEFAULT_LOGIC_FILE
  const logicBytes = await read(logicPath)
  const logic = logicBytes ? logicText(new TextDecoder('utf-8').decode(logicBytes)) : ''
  return { files, text: logic ? `${REQUEST}\n\n${logic}` : REQUEST }
}

/**
 * 选中的文件里有压缩包时，展开成普通文件加一段需求文字；其他文件原样保留。
 * 压缩包只认流程包，不是流程包、或者打不开，就不导入并给出错误。
 */
export async function expandPackages(files: File[]): Promise<{ files: File[]; texts: string[]; error: string }> {
  const result: File[] = []
  const texts: string[] = []
  const errors: string[] = []
  for (const file of files) {
    if (!isZipFile(file)) {
      result.push(file)
      continue
    }
    try {
      const unpacked = await unpackFlowPackage(file)
      if (!unpacked) errors.push(`「${file.name}」不是流程包，没有导入。`)
      else {
        result.push(...unpacked.files)
        texts.push(unpacked.text)
      }
    } catch (err) {
      errors.push(err instanceof Error ? err.message : '流程包无法导入。')
    }
  }
  return { files: result, texts, error: errors.join(' ') }
}

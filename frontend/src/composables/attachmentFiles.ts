/** Shared attachment rules for the picker, drag/drop, and clipboard. */
export const ATTACHMENT_ACCEPT = '.xlsx,.xlsm,.xls,.csv,.png,.jpg,.jpeg,.webp,.gif'
export const MAX_IMAGE_ATTACHMENTS = 10

const workbookExtension = /\.(xlsx|xlsm|xls|csv)$/i
const imageExtension = /\.(png|jpe?g|webp|gif)$/i
const imageMimeExtensions: Record<string, string> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/webp': 'webp',
  'image/gif': 'gif',
}

export function isImageFile(name: string, mediaType = ''): boolean {
  return imageExtension.test(name) || !!imageMimeExtensions[mediaType.toLowerCase()]
}

export function isWorkbookFile(name: string): boolean {
  return workbookExtension.test(name)
}

export function isSupportedAttachment(file: Pick<File, 'name' | 'type'>): boolean {
  return isWorkbookFile(file.name) || imageExtension.test(file.name)
}

interface ClipboardFiles {
  files: ArrayLike<File>
  items?: ArrayLike<{ kind: string; getAsFile(): File | null }>
}

/** Files and items commonly expose the same files; use items only as a fallback. */
export function filesFromClipboard(clipboard: ClipboardFiles | null): File[] {
  if (!clipboard) return []
  const files = Array.from(clipboard.files)
  const source = files.length ? files : Array.from(clipboard.items ?? [])
    .flatMap((item) => item.kind === 'file' ? [item.getAsFile()].filter((file): file is File => !!file) : [])
  return source.map((file, index) => {
    const extension = imageMimeExtensions[file.type.toLowerCase()]
    // Screenshot clipboard blobs do not always carry a usable filename.
    if (!extension || /\.[^./\\]+$/.test(file.name)) return file
    return new File([file], `${file.name || 'pasted-image'}-${index + 1}.${extension}`, {
      type: file.type, lastModified: file.lastModified,
    })
  })
}

export function captureAttachmentPaste(
  event: Pick<ClipboardEvent, 'clipboardData' | 'preventDefault' | 'stopPropagation'>,
  attach: (files: File[]) => void,
): boolean {
  const files = filesFromClipboard(event.clipboardData)
  if (!files.length) return false
  const text = event.clipboardData?.getData('text/plain') ?? ''
  const html = event.clipboardData?.getData('text/html') ?? ''
  // Spreadsheet apps can copy a cell range as both TSV/HTML and a PNG preview.
  // Keep editable table text; actual workbook files still take the attachment path.
  if (files.every((file) => isImageFile(file.name, file.type)) && text &&
    (text.includes('\t') || /<table(?:\s|>)/i.test(html))) return false
  // File-manager clipboards can also contain a path; never insert that as text.
  event.preventDefault()
  event.stopPropagation()
  attach(files)
  return true
}

export function filterAttachmentFiles(files: File[], existingImages = 0): { files: File[]; error: string } {
  const accepted: File[] = []
  let imageCount = existingImages
  let unsupported = false
  let tooManyImages = false
  for (const file of files) {
    if (!isSupportedAttachment(file)) {
      unsupported = true
      continue
    }
    if (isImageFile(file.name, file.type) && ++imageCount > MAX_IMAGE_ATTACHMENTS) {
      tooManyImages = true
      continue
    }
    accepted.push(file)
  }
  return {
    files: accepted,
    error: [unsupported ? '仅支持 Excel、CSV 和 PNG、JPG、WebP、GIF 图片。' : '',
      tooManyImages ? `每条消息最多添加 ${MAX_IMAGE_ATTACHMENTS} 张图片。` : ''].filter(Boolean).join(' '),
  }
}

export function attachmentMessage(text: string, attachments: { name: string; mediaType?: string }[]): string {
  const message = text.trim()
  if (message || !attachments.length) return message
  return attachments.every((file) => isImageFile(file.name, file.mediaType))
    ? '请查看并描述这些图片。' : '请分析这些附件。'
}

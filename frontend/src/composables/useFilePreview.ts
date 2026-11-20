import { useRouter } from 'vue-router'

import type { FileInfo } from '@/api/types'

/**
 * 查看文件：统一在新的浏览器标签页里整页打开（/preview/文件ID），不再在当前页面上盖弹层。
 *
 * 已上传的文件按文件 ID 打开，并尽量带上所属聊天会话 ID，便于在预览页进入当前会话内的表格比对；
 * 还没上传的本地文件（首页刚选中的附件）没有 ID，做成 blob 地址交给新标签页读取。
 */
export function useFilePreview(getConversationId?: () => string | null | undefined) {
  const router = useRouter()
  return function openFilePreview(source: FileInfo | File | string) {
    if (source instanceof File) {
      const target = router.resolve({
        name: 'preview',
        params: { fileId: 'local' },
        query: { blob: URL.createObjectURL(source), name: source.name },
      })
      window.open(target.href, '_blank')
      return
    }
    const fileId = typeof source === 'string' ? source : source.file_id
    const conversationId = getConversationId?.() || (typeof source === 'object' ? source.conversation_id : null)
    const target = router.resolve({
      name: 'preview',
      params: { fileId },
      query: conversationId ? { conversation: conversationId } : undefined,
    })
    window.open(target.href, '_blank')
  }
}

/**
 * 文件下载：存下来的和聊天里查看的都是令牌版，下载时后端统一换回真实内容（见 /files/{id}/download）。
 * 这里只是触发浏览器保存，不再单独提供「脱敏副本」的下载。
 */
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { fileApi } from '@/api'

export function usePrivacyCopy() {
  const route = useRoute()
  /** 只有在聊天页里才有会话；别处（比如客户页）不带 */
  const conversationId = computed(() => (route.name === 'chat' && typeof route.params.id === 'string' ? route.params.id : undefined))
  const downloading = ref<string | null>(null)

  function download(fileId: string, filename: string) {
    const link = document.createElement('a')
    link.href = fileApi.downloadUrl(fileId)
    link.download = filename
    link.rel = 'noopener'
    document.body.append(link)
    link.click()
    link.remove()
  }

  return { conversationId, downloading, download }
}

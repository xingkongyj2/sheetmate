/**
 * 脱敏副本的下载：请求 xlsx → 用临时链接触发保存。聊天页里带上会话 id，
 * 后端据此把「下载了脱敏副本」记到这个会话的处理记录里。
 */
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { fileApi } from '@/api'
import { errorText } from '@/api/http'
import { useToast } from '@/composables/useToast'

/** 原名_脱敏.xlsx：后端没给文件名时用它 */
export function privacyCopyName(filename: string): string {
  const dot = filename.lastIndexOf('.')
  const stem = dot > 0 ? filename.slice(0, dot) : filename
  return `${stem}_脱敏.xlsx`
}

export function usePrivacyCopy() {
  const route = useRoute()
  const toast = useToast()
  /** 只有在聊天页里才有会话；别处（比如客户页）不带 */
  const conversationId = computed(() => (route.name === 'chat' && typeof route.params.id === 'string' ? route.params.id : undefined))
  const downloading = ref<string | null>(null)

  async function download(fileId: string, filename: string) {
    if (downloading.value) return
    downloading.value = fileId
    try {
      const { blob, filename: served } = await fileApi.privacyCopy(fileId, conversationId.value)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = served || privacyCopyName(filename)
      link.rel = 'noopener'
      document.body.append(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      downloading.value = null
    }
  }

  return { conversationId, downloading, download }
}

/** 首页与聊天页共享的输入偏好，按账号保存在当前浏览器。 */
import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

import { browserHomeDraftStorage } from '../composables/homeDraftPersistence.ts'

const THINKING_KEY = 'sheetmate.chat-thinking.v1:'

export const useChatPreferencesStore = defineStore('chatPreferences', () => {
  const thinking = ref(false)
  let owner: string | null = null

  function save() {
    if (owner === null) return
    try {
      localStorage.setItem(THINKING_KEY + owner, JSON.stringify(thinking.value))
    } catch { /* 存储不可用时仍允许使用本次页面里的选择。 */ }
  }

  watch(thinking, save, { flush: 'sync' })

  function hydrate(userId: string | number) {
    const target = String(userId)
    if (owner === target) return
    owner = null
    let saved: boolean | null = null
    try {
      const value = localStorage.getItem(THINKING_KEY + target)
      if (value === 'true' || value === 'false') saved = value === 'true'
    } catch { /* 浏览器可能禁用本地存储。 */ }
    if (saved === null) {
      try {
        const draft = browserHomeDraftStorage.readMetadata(target)
        if (typeof draft?.thinking === 'boolean') saved = draft.thinking
      } catch { /* 旧草稿损坏不影响偏好的初始化。 */ }
    }
    thinking.value = saved ?? false
    owner = target
    if (saved !== null) save()
  }

  /** 退出账号时清空内存，下次登录仍恢复该账号的选择。 */
  function reset() {
    owner = null
    thinking.value = false
  }

  return { thinking, hydrate, reset }
})

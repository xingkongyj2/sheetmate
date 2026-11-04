/** 登录状态 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { authApi } from '@/api'
import type { User } from '@/api/types'
import { useHomeDraftStore } from '@/stores/homeDraft'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const checked = ref(false)

  /** 启动时确认一次登录状态（Cookie 会话） */
  async function ensure(): Promise<User | null> {
    if (checked.value) return user.value
    try {
      user.value = await authApi.me()
    } catch {
      user.value = null
    }
    checked.value = true
    return user.value
  }

  async function login(account: string, password: string) {
    user.value = await authApi.login(account, password)
    checked.value = true
  }

  async function register(account: string, password: string, displayName: string) {
    user.value = await authApi.register(account, password, displayName)
    checked.value = true
  }

  async function logout() {
    await authApi.logout().catch(() => undefined)
    useHomeDraftStore().clear()
    user.value = null
  }

  async function updateProfile(payload: { display_name?: string; profile?: Record<string, unknown> }) {
    user.value = await authApi.updateProfile(payload)
  }

  function clear() {
    useHomeDraftStore().clear()
    user.value = null
    checked.value = true
  }

  return { user, checked, ensure, login, register, logout, updateProfile, clear }
})

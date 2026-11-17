import { readonly, ref } from 'vue'

import { settingsApi } from '@/api'

/** 脱敏总开关的当前状态（设置页改了、对话页顶部的提示立刻跟着变）。null 表示还没读到。 */
const enabled = ref<boolean | null>(null)

export function usePrivacySwitch() {
  async function refresh() {
    try {
      enabled.value = (await settingsApi.getPrivacy()).enabled
    } catch {
      // 读不到就保持原状：不因为接口暂时失败而误报「已关闭」
    }
  }
  return { enabled: readonly(enabled), refresh, set: (value: boolean) => { enabled.value = value } }
}

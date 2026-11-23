<script setup lang="ts">
import { Eye, LoaderCircle } from '@lucide/vue'
import { onMounted, ref } from 'vue'

import { settingsApi } from '@/api'
import { errorText } from '@/api/http'
import type { PrivacyRuleSetting } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import { usePrivacySwitch } from '@/composables/usePrivacySwitch'
import { useToast } from '@/composables/useToast'

/** 脱敏设置：总开关（统一控制文件脱敏与模型脱敏，关闭后不再执行任何脱敏逻辑）+ 每条脱敏规则的开关，规则的匹配逻辑可以查看 */
const toast = useToast()
const shared = usePrivacySwitch()
const enabled = ref(true)
const rules = ref<PrivacyRuleSetting[]>([])
const loaded = ref(false)
const saving = ref(false)
const viewing = ref<PrivacyRuleSetting | null>(null)

function show(data: { enabled: boolean; rules: PrivacyRuleSetting[] }) {
  enabled.value = data.enabled
  rules.value = data.rules
  shared.set(data.enabled)
}

onMounted(async () => {
  try {
    show(await settingsApi.getPrivacy())
  } catch (error) {
    toast.error(errorText(error))
  } finally {
    loaded.value = true
  }
})

async function save(payload: { enabled?: boolean; rules?: Record<string, boolean> }) {
  if (saving.value) return
  saving.value = true
  try {
    show(await settingsApi.savePrivacy(payload))
  } catch (error) {
    toast.error(errorText(error))
    try { show(await settingsApi.getPrivacy()) } catch { /* 保持现状 */ }
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section class="card-soft divide-y divide-line" aria-label="脱敏" data-testid="privacy-card">
    <label class="flex cursor-pointer items-center gap-4 p-5 sm:px-6">
      <span class="min-w-0 flex-1 text-[15px] font-semibold">启用</span>
      <LoaderCircle v-if="saving" class="size-4 shrink-0 animate-spin text-mute" />
      <input
        type="checkbox"
        role="switch"
        class="toggle shrink-0"
        aria-label="脱敏总开关"
        :checked="enabled"
        :disabled="!loaded || saving"
        data-testid="privacy-master"
        @change="save({ enabled: ($event.target as HTMLInputElement).checked })"
      />
    </label>
    <div
      v-for="rule in rules"
      :key="rule.id"
      class="flex items-center gap-3 p-5 transition-opacity sm:px-6"
    >
      <span class="min-w-0 flex-1 text-sm font-medium">{{ rule.name }}</span>
      <button type="button" class="icon-btn size-9" title="查看规则" :aria-label="`查看${rule.name}规则`" :data-testid="`privacy-view-${rule.id}`" @click="viewing = rule">
        <Eye class="size-4" />
      </button>
      <input
        type="checkbox"
        role="switch"
        class="toggle toggle-sm shrink-0"
        :aria-label="`${rule.name}规则`"
        :checked="rule.enabled"
        :disabled="!enabled || saving"
        :data-testid="`privacy-rule-${rule.id}`"
        @change="save({ rules: { [rule.id]: ($event.target as HTMLInputElement).checked } })"
      />
    </div>

    <slot />

    <BaseModal v-if="viewing" :title="viewing.name" width="max-w-2xl" @close="viewing = null">
      <div class="space-y-6" data-testid="privacy-rule-logic">
        <section v-for="section in viewing.logic" :key="section.title">
          <h4 class="mb-2 text-[13px] font-semibold text-mute">{{ section.title }}</h4>
          <ul class="space-y-3">
            <li v-for="item in section.items" :key="item.label">
              <p class="mb-1 text-sm">{{ item.label }}</p>
              <code v-if="item.kind === 'regex'" class="block break-all rounded-xl bg-base-200/60 px-3 py-2 font-mono text-xs leading-relaxed">{{ item.value }}</code>
              <p v-else-if="item.kind === 'words'" class="flex flex-wrap gap-1.5">
                <span v-for="word in (item.value as string[])" :key="word" class="rounded-md bg-base-200/60 px-2 py-0.5 font-mono text-xs">{{ word }}</span>
              </p>
              <p v-else class="text-[13px] text-mute">{{ item.value }}</p>
            </li>
          </ul>
        </section>
      </div>
    </BaseModal>
  </section>
</template>

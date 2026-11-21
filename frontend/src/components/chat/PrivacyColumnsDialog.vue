<script setup lang="ts">
/** 文件的脱敏维护：每一列现在是不是脱敏（规则 + 脱敏模型分析列头和内容的结果）。开关一打开，这一列在原文件里就变成令牌；关掉就还原。
 *  自动识别的列显示自动分析出的类型；手动打开的列可以选类型（电话号码 / 详细地址 / 通用，通用只有手动才能用）。 */
import { LoaderCircle } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { fileApi } from '@/api'
import { errorText } from '@/api/http'
import type { PrivacyColumnChange, PrivacyColumns, PrivacyColumnState } from '@/api/types'
import MascotLoader from '@/components/brand/MascotLoader.vue'
import BaseModal from '@/components/common/BaseModal.vue'

const props = defineProps<{ fileId: string; filename: string; conversationId?: string; elevated?: boolean }>()
const emit = defineEmits<{ close: []; changed: [] }>()

const data = ref<PrivacyColumns | null>(null)
const loading = ref(true)
const busy = ref<string | null>(null)
const error = ref('')
const current = ref(0)

const sheet = computed(() => data.value?.sheets[current.value] ?? null)
// 类型下拉的宽度跟着最长的类型名走（再加上左右留白和箭头），类型多了也不会截断
const typeWidth = computed(() => `calc(${Math.max(2, ...(data.value?.types ?? []).map((type) => [...type.label].length))}em + 3rem)`)
async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await fileApi.privacyColumns(props.fileId, props.conversationId)
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}
onMounted(load)

async function change(column: PrivacyColumnState, patch: Pick<PrivacyColumnChange, 'choice' | 'kind'>) {
  if (!sheet.value || busy.value) return
  busy.value = column.name
  error.value = ''
  try {
    data.value = await fileApi.savePrivacyColumns(props.fileId, [{ sheet: sheet.value.sheet, column: column.name, ...patch }], props.conversationId)
    emit('changed')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = null
  }
}

// 自动识别出来的列不支持手动关闭；手动打开的列可以关
const locked = (column: PrivacyColumnState) => column.masked && column.choice !== 'mask'
const toggle = (column: PrivacyColumnState) => change(column, { choice: column.masked ? 'keep' : 'mask' })
const setKind = (column: PrivacyColumnState, kind: PrivacyColumnChange['kind']) => change(column, { choice: 'mask', kind })
</script>

<template>
  <BaseModal title="脱敏" width="max-w-xl" :elevated="elevated" @close="emit('close')">
    <template #title>
      <h3 class="truncate text-base font-semibold" :title="filename">脱敏 · {{ filename }}</h3>
    </template>
    <div class="min-h-24" data-testid="privacy-columns">
      <MascotLoader v-if="loading" size="sm" label="正在分析列头和内容" class="py-10" />
      <p v-else-if="error && !data" class="py-10 text-center text-sm text-error" role="alert">{{ error }}</p>
      <template v-else-if="data && sheet">
        <div class="mb-3 flex flex-wrap gap-1.5" role="tablist">
          <button
            v-for="(item, index) in data.sheets" :key="item.sheet" type="button" role="tab" :aria-selected="index === current"
            class="rounded-lg px-2.5 py-1 text-[13px] transition" :class="index === current ? 'bg-ink text-white' : 'bg-paper text-mute hover:text-ink'"
            @click="current = index"
          >{{ item.sheet }}<span v-if="item.columns.some((column) => column.masked)" class="ml-1.5 tabular-nums opacity-70">{{ item.columns.filter((column) => column.masked).length }}</span></button>
        </div>
        <ul class="divide-y divide-line">
          <li
            v-for="column in sheet.columns" :key="column.name" class="flex min-h-12 items-center gap-2.5 py-1.5"
            :title="column.reason || undefined" :data-testid="`privacy-column-${column.name}`" :data-masked="column.masked"
          >
            <span class="min-w-0 flex-1 truncate text-sm" :title="column.name">{{ column.name }}</span>
            <select
              v-if="column.choice === 'mask'" class="select select-sm shrink-0 rounded-xl" :style="{ width: typeWidth }" :value="column.kind ?? 'custom'"
              :disabled="busy !== null" :aria-label="`${column.name}的脱敏类型`"
              @change="setKind(column, ($event.target as HTMLSelectElement).value as PrivacyColumnChange['kind'])"
            >
              <option v-for="type in data.types" :key="type.id" :value="type.id" :disabled="!type.enabled">{{ type.label }}</option>
            </select>
            <span v-else-if="column.masked && column.kind_label" class="shrink-0 text-xs text-mute">{{ column.kind_label }}</span>
            <LoaderCircle v-if="busy === column.name" class="size-4 shrink-0 animate-spin text-mute" />
            <input
              v-else type="checkbox" role="switch" class="toggle toggle-sm shrink-0" :checked="column.masked" :disabled="busy !== null || locked(column)" :title="locked(column) ? '自动识别的列不能关闭' : undefined"
              :aria-label="`${column.name}脱敏`" @change="toggle(column)"
            />
          </li>
        </ul>
        <p v-if="error" class="mt-3 text-xs text-error" role="alert">{{ error }}</p>
      </template>
    </div>
  </BaseModal>
</template>

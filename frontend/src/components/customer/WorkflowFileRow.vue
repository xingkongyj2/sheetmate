<script setup lang="ts">
/** 文件页里的一行文件：预览、改名、下载、删除。输入文件和输出文件共用；改名只改名字，扩展名固定。 */
import { Check, Download, FileCheck2, FileSpreadsheet, LoaderCircle, Pencil, Trash2, X } from '@lucide/vue'
import { computed, nextTick, ref } from 'vue'

import { errorText } from '@/api/http'
import type { FileInfo, PrivacyFileStatus } from '@/api/types'
import PrivacyStatusIcon from '@/components/chat/PrivacyStatusIcon.vue'
import { timeAgo } from '@/composables/chatModel'

const props = defineProps<{
  file: FileInfo
  badge?: string
  badgeClass?: string
  /** 这个文件的脱敏状态；不是表格文件（没有状态）就不显示图标 */
  privacy?: PrivacyFileStatus | null
  /** 不传就不显示改名按钮；失败时抛出错误，这一行会把原因显示在下面 */
  renameFile?: (file: FileInfo, filename: string) => Promise<void>
}>()
const emit = defineEmits<{ preview: [file: FileInfo]; remove: [file: FileInfo] }>()

const MAX_STEM = 100
const editing = ref(false)
const saving = ref(false)
const draft = ref('')
const error = ref('')
const input = ref<HTMLInputElement | null>(null)
const extension = computed(() => {
  const dot = props.file.filename.lastIndexOf('.')
  return dot > 0 ? props.file.filename.slice(dot) : ''
})
const stem = computed(() => props.file.filename.slice(0, props.file.filename.length - extension.value.length))

async function startEdit() {
  draft.value = stem.value
  error.value = ''
  editing.value = true
  await nextTick()
  input.value?.focus()
  input.value?.select()
}
function cancel() {
  if (saving.value) return
  editing.value = false
  error.value = ''
}
async function save() {
  if (saving.value || !props.renameFile) return
  const name = draft.value.trim()
  // 不小心连扩展名一起粘进来时不要重复追加
  const full = extension.value && name.toLowerCase().endsWith(extension.value.toLowerCase()) ? name : name + extension.value
  if (!name || full === props.file.filename) { cancel(); return }
  saving.value = true
  error.value = ''
  try {
    await props.renameFile(props.file, full)
    editing.value = false
  } catch (err) {
    error.value = errorText(err)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="-mx-2 rounded-xl px-2 py-2 text-sm transition hover:bg-paper">
    <form v-if="editing" class="flex items-center gap-2" @submit.prevent="save">
      <FileCheck2 v-if="file.kind === 'expected'" class="size-4 shrink-0 text-[#b7860b]" />
      <FileSpreadsheet v-else class="size-4 shrink-0 text-fun-green" />
      <input ref="input" v-model="draft" class="input input-sm min-w-0 flex-1 rounded-xl" aria-label="文件名" :maxlength="MAX_STEM" :disabled="saving" @keydown.esc.stop.prevent="cancel" />
      <span v-if="extension" class="shrink-0 text-faint">{{ extension }}</span>
      <div class="flex shrink-0 items-center gap-0.5">
        <button type="submit" class="icon-btn !text-[#6265dc]" :disabled="saving" aria-label="保存文件名" title="保存"><LoaderCircle v-if="saving" class="size-4 animate-spin" /><Check v-else class="size-4" /></button>
        <button type="button" class="icon-btn" :disabled="saving" aria-label="取消" title="取消" @click="cancel"><X class="size-4" /></button>
      </div>
    </form>
    <div v-else class="flex items-center gap-3">
      <button type="button" class="flex min-w-0 flex-1 items-center gap-2 rounded text-left underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ink" :title="file.filename" :aria-label="`预览 ${file.filename}`" @click="emit('preview', file)">
        <FileCheck2 v-if="file.kind === 'expected'" class="size-4 shrink-0 text-[#b7860b]" />
        <FileSpreadsheet v-else class="size-4 shrink-0 text-fun-green" /><span class="truncate">{{ file.filename }}</span>
      </button>
      <span v-if="badge" class="tag shrink-0 whitespace-nowrap" :class="badgeClass">{{ badge }}</span>
      <span class="shrink-0 text-xs text-faint">{{ timeAgo(file.created_at) }}</span>
      <PrivacyStatusIcon :status="privacy" class="-mx-1" />
      <div class="flex shrink-0 items-center gap-0.5">
        <button v-if="renameFile" type="button" class="icon-btn" title="修改文件名" :aria-label="`修改 ${file.filename} 的文件名`" @click="startEdit"><Pencil class="size-4" /></button>
        <a :href="file.download_url" class="icon-btn" title="下载" :aria-label="`下载 ${file.filename}`"><Download class="size-4" /></a>
        <button type="button" class="icon-btn hover:text-error" title="删除" :aria-label="`删除 ${file.filename}`" @click="emit('remove', file)"><Trash2 class="size-4" /></button>
      </div>
    </div>
    <p v-if="error" class="mt-1 pl-6 text-xs text-error" role="alert">{{ error }}</p>
  </div>
</template>

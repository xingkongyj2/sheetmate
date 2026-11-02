<script setup lang="ts">
import { Check, Copy, Download, FileCode2, LoaderCircle } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import { scriptApi } from '@/api'
import { errorText } from '@/api/http'
import type { ScriptFile, ScriptReference } from '@/api/types'
import { scriptTitle } from '@/composables/scriptPresentation'
import { useToast } from '@/composables/useToast'

import BaseModal from './BaseModal.vue'

const props = withDefaults(defineProps<{ script: ScriptReference; compact?: boolean }>(), { compact: false })
const toast = useToast()
const opened = ref(false)
const loading = ref(false)
const error = ref('')
const file = ref<ScriptFile | null>(null)
const copied = ref(false)
const title = computed(() => scriptTitle(file.value?.script_title || props.script.script_title))

watch(() => props.script.script_id, () => { file.value = null; error.value = ''; opened.value = false; copied.value = false })

async function load() {
  opened.value = true
  if (file.value || loading.value) return
  loading.value = true
  error.value = ''
  const id = props.script.script_id
  try {
    const result = await scriptApi.get(id)
    if (id === props.script.script_id) file.value = result
  } catch (err) {
    if (id === props.script.script_id) error.value = errorText(err)
  } finally { loading.value = false }
}

async function copy() {
  if (!file.value) return
  try { await navigator.clipboard.writeText(file.value.code); copied.value = true }
  catch { toast.error('复制失败，请选中脚本内容复制。') }
}

function download() {
  if (!file.value) return
  const url = URL.createObjectURL(new Blob([file.value.code], { type: 'text/x-python;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `${file.value.script_id}.py`
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
</script>

<template>
  <button type="button" class="flex min-w-0 max-w-full items-start gap-2 rounded-lg text-left transition hover:bg-fun-purple/10 focus-visible:outline-2 focus-visible:outline-fun-purple" :class="compact ? 'px-1.5 py-1 text-[11px]' : 'w-full border border-line bg-white px-3 py-2.5 text-xs'" :title="`查看脚本：${script.script_id}.py`" @click.stop="load">
    <FileCode2 class="mt-0.5 size-4 shrink-0 text-fun-purple" />
    <span class="min-w-0 flex-1">
      <span class="block break-words font-medium text-fun-purple" :class="compact ? 'line-clamp-2' : ''">{{ scriptTitle(script.script_title) }}</span>
      <span class="block font-mono text-[10px] leading-4 text-faint" :class="compact ? 'truncate' : 'break-all'">{{ script.script_id }}.py</span>
    </span>
  </button>

  <BaseModal v-if="opened" width="max-w-5xl" @close="opened = false">
    <template #title>
      <div class="flex items-start gap-2">
        <FileCode2 class="mt-0.5 size-5 shrink-0 text-fun-purple" />
        <div class="min-w-0"><h3 class="break-words text-base font-semibold">{{ title }}</h3><p class="mt-1 break-all font-mono text-[11px] text-faint">{{ script.script_id }}.py</p></div>
      </div>
    </template>
    <p v-if="loading" role="status" class="flex items-center justify-center gap-2 py-10 text-sm text-mute"><LoaderCircle class="size-4 animate-spin" />正在加载脚本…</p>
    <div v-else-if="error" role="alert" class="space-y-3 rounded-xl bg-error/5 p-4 text-sm text-error"><p>{{ error }}</p><button type="button" class="btn-line" @click="load">重新加载</button></div>
    <pre v-else-if="file" class="max-h-[60vh] overflow-auto rounded-xl bg-[#171d28] p-4 font-mono text-xs leading-6 text-[#dde7f5]" tabindex="0"><code>{{ file.code }}</code></pre>
    <template v-if="file" #footer>
      <button type="button" class="btn-line" @click="copy"><Check v-if="copied" class="size-4" /><Copy v-else class="size-4" />{{ copied ? '已复制' : '复制代码' }}</button>
      <button type="button" class="btn-ink" @click="download"><Download class="size-4" />下载 .py 文件</button>
    </template>
  </BaseModal>
</template>

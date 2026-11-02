<script setup lang="ts">
import { Check, ChevronDown, CircleAlert, Copy, FileCode2, LoaderCircle, Search } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import type { UiMessage } from '@/composables/chatModel'
import ScriptFile from '@/components/common/ScriptFile.vue'
import { buildExecutionLogs, exportExecutionLogs, filterExecutionLogs, formatLogValue, type LogFilter } from '@/composables/executionLogs'
import { useToast } from '@/composables/useToast'

const props = defineProps<{ message: UiMessage | null }>()
const toast = useToast()
const query = ref('')
const filter = ref<LogFilter>('all')
const expanded = ref<Record<string, boolean>>({})
const copied = ref('')
const entries = computed(() => buildExecutionLogs(props.message))
const visible = computed(() => filterExecutionLogs(entries.value, filter.value, query.value))
const failures = computed(() => entries.value.filter((entry) => entry.status === 'error').length)
const tools = computed(() => entries.value.filter((entry) => entry.category === 'tool'))
const elapsed = computed(() => tools.value.reduce((total, entry) => total + (entry.duration ?? 0), 0))
const filters: { value: LogFilter; label: string }[] = [{ value: 'all', label: '全部' }, { value: 'tools', label: '工具调用' }, { value: 'errors', label: '仅错误' }]
const statusLabels = { running: '运行中', success: '成功', error: '错误', warning: '需关注', info: '事件' }

watch(() => props.message?.key, () => { query.value = ''; filter.value = 'all'; expanded.value = {}; copied.value = '' })

function duration(ms: number) {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`
}

function isExpanded(key: string) {
  return expanded.value[key] ?? false
}

async function copy(text: string, key: string) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = key
    window.setTimeout(() => { if (copied.value === key) copied.value = '' }, 1800)
  } catch { toast.error('复制失败，请选中日志内容复制。') }
}
</script>

<template>
  <div class="space-y-4">
    <template v-if="entries.length">
      <div class="space-y-2">
        <div class="flex items-center justify-between gap-2 text-[11px] text-mute">
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span class="font-mono">{{ tools.length }} 次调用</span>
            <span :class="failures ? 'text-error' : ''">{{ failures }} 个错误</span>
            <span v-if="tools.length" class="font-mono">{{ duration(elapsed) }}</span>
            <span v-if="message" class="font-mono text-[10px] text-faint" :title="message.createdAt">#{{ message.id ?? message.key }}</span>
          </div>
          <button type="button" class="icon-btn shrink-0" title="复制全部日志 JSON" aria-label="复制全部日志 JSON" @click="message && copy(exportExecutionLogs(message, entries), 'all')">
            <Check v-if="copied === 'all'" class="size-3.5 text-success" /><Copy v-else class="size-3.5" />
          </button>
        </div>
        <label class="flex items-center gap-2 rounded-xl border border-line bg-white px-3 py-2 focus-within:border-ink/35">
          <Search class="size-3.5 shrink-0 text-faint" />
          <input v-model="query" type="search" class="min-w-0 flex-1 bg-transparent text-xs outline-none" placeholder="搜索日志…" aria-label="搜索执行日志" />
        </label>
        <div class="flex items-center justify-between gap-2">
          <div class="flex gap-1">
            <button v-for="item in filters" :key="item.value" type="button" class="rounded-lg px-2.5 py-1.5 text-[11px] transition" :class="filter === item.value ? 'bg-ink text-white' : 'text-mute hover:bg-paper'" :aria-pressed="filter === item.value" @click="filter = item.value">{{ item.label }}</button>
          </div>
          <span class="shrink-0 font-mono text-[10px] text-faint">{{ visible.length }} / {{ entries.length }}</span>
        </div>
      </div>

      <ol class="space-y-2">
        <li v-for="entry in visible" :key="entry.key" class="min-w-0 overflow-hidden rounded-xl border bg-white" :class="entry.status === 'error' ? 'border-error/25' : 'border-line'">
          <button type="button" class="flex w-full items-start gap-2.5 p-3 text-left transition hover:bg-paper/50" :aria-expanded="isExpanded(entry.key)" @click="expanded[entry.key] = !isExpanded(entry.key)">
            <span class="pt-0.5 font-mono text-[10px] text-faint">{{ String(entry.sequence).padStart(2, '0') }}</span>
            <div class="min-w-0 flex-1">
              <div class="flex items-start justify-between gap-2">
                <span class="min-w-0 break-all font-mono text-[12px] font-semibold leading-5">{{ entry.title }}</span>
                <span class="flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px]" :class="entry.status === 'error' ? 'bg-error/10 text-error' : entry.status === 'success' ? 'bg-success/10 text-success' : entry.status === 'running' ? 'bg-info/10 text-info' : entry.status === 'warning' ? 'bg-warning/10 text-[#8a5a00]' : 'bg-paper text-mute'">
                  <LoaderCircle v-if="entry.status === 'running'" class="size-3 animate-spin" /><CircleAlert v-else-if="entry.status === 'error'" class="size-3" />{{ statusLabels[entry.status] }}
                </span>
              </div>
              <div class="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 font-mono text-[10px] text-faint">
                <span>{{ entry.type }}</span><span v-if="entry.callId" class="break-all">{{ entry.callId }}</span><span v-if="entry.duration !== undefined">{{ duration(entry.duration) }}</span>
              </div>
            </div>
            <ChevronDown class="mt-0.5 size-3.5 shrink-0 text-faint transition" :class="isExpanded(entry.key) ? 'rotate-180' : ''" />
          </button>
          <div v-if="entry.scripts.length" class="space-y-1 px-3 pb-3">
            <ScriptFile v-for="script in entry.scripts" :key="script.script_id" :script="script" compact />
          </div>
          <div v-if="isExpanded(entry.key)" class="space-y-3 border-t border-line bg-paper/40 p-3">
            <p v-if="entry.summary" class="break-words text-[11px] leading-5 [overflow-wrap:anywhere]" :class="entry.status === 'error' ? 'text-error' : 'text-mute'">{{ entry.summary }}</p>
            <div v-for="(part, index) in entry.sections" :key="`${entry.key}-${index}`" class="min-w-0">
              <div class="mb-1.5 flex items-center justify-between gap-2">
                <span class="flex items-center gap-1.5 text-[11px] font-medium"><FileCode2 class="size-3" />{{ part.label }}<span class="font-mono text-[9px] text-faint">{{ part.language }}</span></span>
                <button type="button" class="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-mute hover:bg-white" :aria-label="`复制${part.label}`" @click="copy(part.text, `${entry.key}-${index}`)"><Check v-if="copied === `${entry.key}-${index}`" class="size-3 text-success" /><Copy v-else class="size-3" />{{ copied === `${entry.key}-${index}` ? '已复制' : '复制' }}</button>
              </div>
              <pre class="max-h-96 overflow-auto rounded-lg bg-[#171d28] p-3 font-mono text-[11px] leading-[1.75] text-[#dde7f5]" tabindex="0"><code>{{ part.text }}</code></pre>
            </div>
            <details class="min-w-0">
              <summary class="cursor-pointer text-[11px] text-mute">原始事件 · JSON</summary>
              <div class="mt-1.5 flex justify-end"><button type="button" class="flex items-center gap-1 text-[10px] text-mute hover:text-ink" @click="copy(formatLogValue(entry.raw), `${entry.key}-raw`)"><Copy class="size-3" />复制原始事件</button></div>
              <pre class="mt-1.5 max-h-96 overflow-auto rounded-lg bg-[#171d28] p-3 font-mono text-[11px] leading-[1.75] text-[#dde7f5]" tabindex="0"><code>{{ formatLogValue(entry.raw) }}</code></pre>
            </details>
          </div>
        </li>
      </ol>
      <p v-if="!visible.length" class="py-10 text-center text-xs text-faint">没有匹配的日志</p>
    </template>
    <p v-else class="py-14 text-center text-sm text-faint">暂无执行日志</p>
  </div>
</template>

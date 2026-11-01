<script setup lang="ts">
/** 一个流程的运行记录；查看详情或结果后返回列表。 */
import { ChevronRight, Download, Eye, LoaderCircle } from '@lucide/vue'
import { defineAsyncComponent, onBeforeUnmount, onMounted, ref } from 'vue'

import { fileApi, runApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo, RunBrief } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import { duration, percent } from '@/composables/chatModel'
import { useToast } from '@/composables/useToast'

import RunDetailDialog from './RunDetailDialog.vue'

const WorkbookPreview = defineAsyncComponent(() => import('@/features/workbook-preview/WorkbookPreview.vue'))

const props = defineProps<{ recipeId: string; recipeName: string; customerId?: string | null }>()
const emit = defineEmits<{ close: []; changed: [] }>()
const toast = useToast()
const runs = ref<RunBrief[]>([])
const loading = ref(true)
const loadError = ref('')
const selectedRun = ref<string | null>(null)
const preview = ref<FileInfo | null>(null)
const loadingPreview = ref<string | null>(null)
let requestVersion = 0
let active = true

async function load() {
  const version = ++requestVersion
  loading.value = true
  loadError.value = ''
  try {
    const records = await runApi.list({ recipe_id: props.recipeId, customer_id: props.customerId ?? undefined })
    if (version === requestVersion) runs.value = records
  } catch (error) {
    if (version === requestVersion) loadError.value = errorText(error)
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

async function previewOutput(fileId: string) {
  if (loadingPreview.value) return
  loadingPreview.value = fileId
  try {
    const file = await fileApi.get(fileId)
    if (active) preview.value = file
  } catch (error) {
    if (active) toast.error(errorText(error))
  } finally {
    if (active) loadingPreview.value = null
  }
}

async function onChanged() {
  emit('changed')
  await load()
}

function runTime(run: RunBrief): string {
  return run.created_at.replace('T', ' ').slice(0, 16)
}

onMounted(load)
onBeforeUnmount(() => {
  active = false
  requestVersion++
})
</script>

<template>
  <BaseModal v-if="!selectedRun && !preview" :title="`${recipeName} · 运行记录`" width="max-w-3xl" @close="emit('close')">
    <p class="mb-4 text-xs leading-5 text-mute">显示该流程最近 50 条运行记录，按时间从新到旧排列。</p>
    <div v-if="loading" class="flex h-40 items-center justify-center" aria-label="正在加载运行记录"><LoaderCircle class="size-5 animate-spin text-mute" /></div>
    <div v-else-if="loadError" class="py-10 text-center" role="alert">
      <p class="text-sm text-error">{{ loadError }}</p>
      <button type="button" class="btn-line mt-3" @click="load">重新加载</button>
    </div>
    <p v-else-if="!runs.length" class="py-12 text-center text-sm text-faint">这个流程还没有运行记录</p>
    <div v-else class="space-y-3">
      <article v-for="run in runs" :key="run.run_id" class="rounded-2xl border border-line p-3 sm:p-4">
        <button
          type="button"
          class="block w-full rounded-xl p-2 text-left transition hover:bg-paper/60 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink disabled:cursor-wait"
          :disabled="!!loadingPreview"
          :aria-label="`查看 ${recipeName} 在 ${runTime(run)} 的运行详情`"
          @click="selectedRun = run.run_id"
        >
          <span class="flex flex-wrap items-center gap-x-2 gap-y-1.5">
            <time :datetime="run.created_at" class="text-[13px] font-medium">{{ runTime(run) }}</time>
            <span class="tag">{{ run.kind_label }}</span>
            <span v-if="run.recipe_version" class="tag">v{{ run.recipe_version }}</span>
            <StatusPill class="ml-auto shrink-0" :status="run.status" :label="run.status_label" />
            <ChevronRight class="size-4 shrink-0 text-faint" />
          </span>
          <span class="mt-2 block break-words text-[13px] leading-6 text-mute">{{ run.request_text || run.summary || run.tool_sequence || '查看每一步的执行详情' }}</span>
        </button>
        <div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line/60 px-2 pt-3 text-xs text-faint">
          <span>比对 {{ run.verification ? percent(run.verification.overall_match_rate) : '—' }}</span>
          <span>耗时 {{ duration(run.duration_ms) || '—' }}</span>
          <div v-if="run.output_file_id" class="ml-auto flex items-center gap-1">
            <button type="button" class="btn-text text-xs" :disabled="!!loadingPreview" :aria-busy="loadingPreview === run.output_file_id" @click="previewOutput(run.output_file_id)">
              <LoaderCircle v-if="loadingPreview === run.output_file_id" class="size-3.5 animate-spin" /><Eye v-else class="size-3.5" />查看结果
            </button>
            <a :href="fileApi.downloadUrl(run.output_file_id)" class="btn-text text-xs"><Download class="size-3.5" />下载结果</a>
          </div>
        </div>
      </article>
    </div>
  </BaseModal>
  <RunDetailDialog v-if="selectedRun" :run-id="selectedRun" @close="selectedRun = null" @changed="onChanged" />
  <WorkbookPreview v-else-if="preview" :key="preview.file_id" :file="preview" @close="preview = null" />
</template>

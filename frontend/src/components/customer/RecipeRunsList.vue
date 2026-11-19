<script setup lang="ts">
/** 一个流程的运行记录列表（嵌在流程详情页里）；点开查看每一步的详情或结果。 */
import { ChevronRight, Download, Eye, LoaderCircle } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { fileApi, runApi } from '@/api'
import { errorText } from '@/api/http'
import type { RunBrief } from '@/api/types'
import StatusPill from '@/components/common/StatusPill.vue'
import { duration, percent } from '@/composables/chatModel'
import { primaryRunDeliveryId } from '@/composables/deliveryFiles'

import { useFilePreview } from '@/composables/useFilePreview'
import RunDetailDialog from './RunDetailDialog.vue'

const props = defineProps<{ recipeId: string; recipeName: string; customerId?: string | null }>()
const emit = defineEmits<{ changed: [] }>()
const runs = ref<RunBrief[]>([])
const visibleRuns = computed(() => runs.value.map((run) => ({ ...run, output_file_id: primaryRunDeliveryId(run) })))
const loading = ref(true)
const loadError = ref('')
const selectedRun = ref<string | null>(null)
const openFilePreview = useFilePreview()
let requestVersion = 0

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

function previewOutput(fileId: string) {
  openFilePreview(fileId)
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
  requestVersion++
})
</script>

<template>
  <div>
    <div v-if="loading" class="flex h-40 items-center justify-center" aria-label="正在加载运行记录"><LoaderCircle class="size-5 animate-spin text-mute" /></div>
    <div v-else-if="loadError" class="py-10 text-center" role="alert">
      <p class="text-sm text-error">{{ loadError }}</p>
      <button type="button" class="btn-line mt-3" @click="load">重新加载</button>
    </div>
    <p v-else-if="!runs.length" class="py-12 text-center text-sm text-faint">还没有运行记录</p>
    <ul v-else class="divide-y divide-line">
      <li v-for="run in visibleRuns" :key="run.run_id" class="py-3 first:pt-0 last:pb-0">
        <button
          type="button"
          class="-mx-2 block w-[calc(100%+1rem)] rounded-xl px-2 py-2 text-left transition hover:bg-paper focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink disabled:cursor-wait"
          :aria-label="`查看 ${recipeName} 在 ${runTime(run)} 的运行详情`"
          @click="selectedRun = run.run_id"
        >
          <span class="flex flex-wrap items-center gap-x-2 gap-y-1.5">
            <time :datetime="run.created_at" class="text-[13px] font-semibold">{{ runTime(run) }}</time>
            <span v-if="run.recipe_version" class="tag">v{{ run.recipe_version }}</span>
            <StatusPill class="ml-auto shrink-0" :status="run.status" :label="run.status_label" />
            <ChevronRight class="size-4 shrink-0 text-faint" />
          </span>
          <span class="mt-1.5 block break-words text-[13px] leading-6 text-mute">{{ run.request_text || run.summary || run.tool_sequence || '查看每一步的执行详情' }}</span>
          <span v-if="run.input_files?.length" class="mt-0.5 block truncate text-xs text-mute" :title="run.input_files.map((file) => file.filename).join('、')">输入：{{ run.input_files.map((file) => file.filename).join('、') }}</span>
          <span class="mt-1 block text-xs text-faint">比对 {{ run.verification ? percent(run.verification.overall_match_rate) : '—' }} · 耗时 {{ duration(run.duration_ms) || '—' }}</span>
        </button>
        <div v-if="run.output_file_id" class="mt-1 flex items-center gap-1">
          <button type="button" class="btn-text text-xs" @click="previewOutput(run.output_file_id)">
            <Eye class="size-3.5" />查看结果
          </button>
          <a :href="fileApi.downloadUrl(run.output_file_id)" class="btn-text text-xs"><Download class="size-3.5" />下载结果</a>
        </div>
      </li>
    </ul>
    <RunDetailDialog v-if="selectedRun" :run-id="selectedRun" @close="selectedRun = null" @changed="onChanged" />
  </div>
</template>

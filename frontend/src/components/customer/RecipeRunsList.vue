<script setup lang="ts">
/**
 * 一个流程的运行记录（流程详情页「运行记录」标签）：按天分组——今天、昨天，再往前显示日期；
 * 每组 = 卡片外的大标题 + 一张卡片，卡片里一行一次运行，点开看每一步详情。
 */
import { ChevronRight, Download, Eye } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { fileApi, runApi } from '@/api'
import { errorText } from '@/api/http'
import type { RunBrief } from '@/api/types'
import MascotLoader from '@/components/brand/MascotLoader.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import { duration, percent } from '@/composables/chatModel'
import { primaryRunDeliveryId } from '@/composables/deliveryFiles'
import { useFilePreview } from '@/composables/useFilePreview'

import RunDetailDialog from './RunDetailDialog.vue'

const props = defineProps<{ recipeId: string; recipeName: string; customerId?: string | null }>()
const emit = defineEmits<{ changed: [] }>()
const runs = ref<RunBrief[]>([])
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

async function onChanged() {
  emit('changed')
  await load()
}

// ---- 按天分组 ----
const pad = (n: number) => String(n).padStart(2, '0')
const dayKey = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
function dayLabel(date: Date): string {
  const today = new Date()
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1)
  if (dayKey(date) === dayKey(today)) return '今天'
  if (dayKey(date) === dayKey(yesterday)) return '昨天'
  const md = `${date.getMonth() + 1}月${date.getDate()}日`
  return date.getFullYear() === today.getFullYear() ? md : `${date.getFullYear()}年${md}`
}

type Row = RunBrief & { output_file_id: string | null; time: string; meta: string }
const groups = computed(() => {
  const result: { key: string; label: string; runs: Row[] }[] = []
  for (const run of runs.value) {
    const date = new Date(run.created_at)
    const key = Number.isNaN(date.getTime()) ? run.created_at.slice(0, 10) : dayKey(date)
    let group = result.find((item) => item.key === key)
    if (!group) {
      group = { key, label: Number.isNaN(date.getTime()) ? key : dayLabel(date), runs: [] }
      result.push(group)
    }
    group.runs.push({
      ...run,
      output_file_id: primaryRunDeliveryId(run),
      time: Number.isNaN(date.getTime()) ? run.created_at.slice(11, 16) : `${pad(date.getHours())}:${pad(date.getMinutes())}`,
      meta: [run.verification ? `比对 ${percent(run.verification.overall_match_rate)}` : '', duration(run.duration_ms)].filter(Boolean).join(' · '),
    })
  }
  return result
})

onMounted(load)
onBeforeUnmount(() => { requestVersion++ })
</script>

<template>
  <div>
    <MascotLoader v-if="loading" size="sm" class="h-36" />
    <div v-else-if="loadError" class="card-soft py-8 text-center" role="alert">
      <p class="text-sm text-error">{{ loadError }}</p>
      <button type="button" class="btn-line mt-3" @click="load">重新加载</button>
    </div>
    <p v-else-if="!runs.length" class="card-soft py-10 text-center text-sm text-faint">还没有运行过</p>
    <div v-else class="space-y-9">
      <section v-for="group in groups" :key="group.key">
        <h2 class="run-day">{{ group.label }}<small>{{ group.runs.length }}</small></h2>
        <ul class="run-card">
          <li v-for="run in group.runs" :key="run.run_id" class="run-row">
            <!-- 整行可点：一个铺满整行的按钮打开详情；查看 / 下载按钮浮在它上面 -->
            <button type="button" class="run-hit" :aria-label="`查看 ${recipeName} 在 ${group.label} ${run.time} 的运行详情`" @click="selectedRun = run.run_id" />
            <div class="pointer-events-none min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <time :datetime="run.created_at" class="text-sm font-semibold tabular-nums">{{ run.time }}</time>
                <span v-if="run.recipe_version" class="tag">v{{ run.recipe_version }}</span>
                <StatusPill :status="run.status" :label="run.status_label" />
                <span v-if="run.meta" class="text-xs tabular-nums text-faint">{{ run.meta }}</span>
              </div>
              <p
                v-if="run.input_files?.length"
                class="mt-0.5 truncate text-[13px] text-mute"
                :title="run.input_files.map((file) => file.filename).join('、')"
              >{{ run.input_files.map((file) => file.filename).join('、') }}</p>
            </div>
            <span v-if="run.output_file_id" class="relative z-[1] flex shrink-0 items-center">
              <button type="button" class="icon-btn" title="查看结果" aria-label="查看结果" @click="openFilePreview(run.output_file_id)"><Eye class="size-4" /></button>
              <a :href="fileApi.downloadUrl(run.output_file_id)" class="icon-btn" title="下载结果" aria-label="下载结果"><Download class="size-4" /></a>
            </span>
            <ChevronRight class="pointer-events-none size-4 shrink-0 text-faint" />
          </li>
        </ul>
      </section>
    </div>
    <RunDetailDialog v-if="selectedRun" :run-id="selectedRun" @close="selectedRun = null" @changed="onChanged" />
  </div>
</template>

<style scoped>
.run-day { display: flex; align-items: baseline; gap: 8px; margin: 0 2px 12px; color: #14141c; font-size: 18px; font-weight: 700; line-height: 1.35; }
.run-day small { color: #a4a4b2; font-size: 13px; font-weight: 500; font-variant-numeric: tabular-nums; }
.run-card { margin: 0; border: 1px solid var(--color-line); border-radius: 16px; background: #fff; padding: 8px; list-style: none; }
.run-row { position: relative; display: flex; align-items: center; gap: 12px; border-radius: 12px; padding: 12px; transition: background-color .15s; }
.run-row:hover { background: var(--color-paper); }
.run-row + .run-row::before { content: ''; position: absolute; top: 0; left: 12px; right: 12px; border-top: 1px solid #f0f0f4; }
.run-row:hover::before, .run-row:hover + .run-row::before { border-color: transparent; }
.run-hit { position: absolute; inset: 0; border-radius: 12px; }
.run-hit:focus-visible { outline: 2px solid var(--color-ink); outline-offset: 2px; }
.run-row .icon-btn:hover { background: #fff; }
</style>

<script setup lang="ts">
/** 一次运行的详情：每一步的输入输出、提示、修复记录；可以查看中间表、修改参数后从某一步重跑（断点续跑） */
import { Download, LoaderCircle } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { fileApi, runApi } from '@/api'
import { errorText } from '@/api/http'
import type { RunDetail, StepReport } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import StepDetail from '@/components/recipe/StepDetail.vue'
import { duration, percent } from '@/composables/chatModel'
import { useToast } from '@/composables/useToast'

const props = defineProps<{ runId: string }>()
const emit = defineEmits<{ close: []; changed: [] }>()
const toast = useToast()

const run = ref<RunDetail | null>(null)
const tables = ref<{ name: string; rows: number }[]>([])
const selected = ref<StepReport | null>(null)
const rerunning = ref(false)
const log = ref<string[]>([])

async function load() {
  try {
    const data = await runApi.get(props.runId)
    run.value = data.run
    tables.value = data.tables
  } catch (err) {
    toast.error(errorText(err))
  }
}
onMounted(load)

const hasTables = computed(() => tables.value.length > 0)

async function rerun(step: StepReport, params: Record<string, unknown>) {
  selected.value = null
  rerunning.value = true
  log.value = [`从第 ${step.step_id} 步重跑…`]
  const handle = runApi.rerun(props.runId, step.step_id, params, (e) => {
    if (e.type === 'recipe_step_done') log.value.push(`第 ${e.step_id} 步完成：${e.rows_out} 行`)
    if (e.type === 'recipe_step_error') log.value.push(`第 ${e.step_id} 步出错：${e.error}`)
    if (e.type === 'recipe_run_done') log.value.push(`结束：${e.summary}`)
    if (e.type === 'recipe_saved') log.value.push(`参数修改已保存为流程 v${e.version}（草稿）`)
  })
  try {
    await handle.done
  } catch (err) {
    log.value.push(errorText(err))
  } finally {
    rerunning.value = false
    await load()
    emit('changed')
  }
}
</script>

<template>
  <BaseModal width="max-w-4xl" @close="emit('close')">
    <template #title>
      <div v-if="run" class="flex items-center gap-2">
        <h3 class="text-base font-semibold">{{ run.kind_label }} · {{ run.created_at.replace('T', ' ').slice(0, 16) }}</h3>
        <StatusPill :status="run.status" :label="run.status_label" />
      </div>
    </template>
    <div v-if="!run" class="flex h-40 items-center justify-center"><LoaderCircle class="size-5 animate-spin text-mute" /></div>
    <div v-else class="space-y-5">
      <div class="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div class="rounded-xl bg-paper px-3 py-2"><div class="text-[11px] text-faint">流程版本</div><div>{{ run.recipe_version ? `v${run.recipe_version}` : '—' }}</div></div>
        <div class="rounded-xl bg-paper px-3 py-2"><div class="text-[11px] text-faint">耗时</div><div>{{ duration(run.duration_ms) || '—' }}</div></div>
        <div class="rounded-xl bg-paper px-3 py-2"><div class="text-[11px] text-faint">与人工结果比对</div><div>{{ run.verification ? percent(run.verification.overall_match_rate) : '—' }}</div></div>
        <div class="rounded-xl bg-paper px-3 py-2"><div class="text-[11px] text-faint">运行校验</div><div class="truncate">{{ run.validation?.summary ?? '—' }}</div></div>
      </div>
      <p v-if="run.request_text" class="text-sm text-mute">{{ run.request_text }}</p>
      <div v-if="run.summary || run.error" class="whitespace-pre-wrap rounded-xl bg-error/5 px-3 py-2.5 text-[13px] text-error">{{ run.summary || run.error }}</div>

      <div v-if="run.steps.length">
        <div class="label-mini mb-2">每一步{{ hasTables ? '（点开可以查看中间表、修改参数后从这一步重跑）' : '' }}</div>
        <div class="space-y-1.5">
          <button
            v-for="s in run.steps"
            :key="s.step_id ?? (s as unknown as { tool: string }).tool"
            class="flex w-full items-center gap-3 rounded-xl border border-line px-3 py-2.5 text-left hover:bg-paper"
            @click="s.step_id ? (selected = s) : undefined"
          >
            <span class="flex size-6 items-center justify-center rounded-full bg-ink text-[11px] font-semibold text-white">{{ s.step_id ?? '·' }}</span>
            <span class="min-w-0 flex-1">
              <span class="block truncate text-[13px] font-medium">{{ s.name ?? (s as unknown as { label: string }).label }}</span>
              <span class="block truncate text-xs text-mute">{{ s.description ?? (s as unknown as { summary: string }).summary }}</span>
            </span>
            <span v-if="s.rows_out !== undefined" class="font-mono text-[11px] text-faint">{{ s.rows_out }} 行</span>
            <StatusPill v-if="s.status" :status="s.status" />
          </button>
        </div>
      </div>

      <div v-if="log.length" class="rounded-xl bg-paper p-3 font-mono text-xs leading-relaxed text-mute">
        <div v-for="(l, i) in log" :key="i">{{ l }}</div>
        <LoaderCircle v-if="rerunning" class="mt-1 size-3.5 animate-spin" />
      </div>
    </div>
    <template v-if="run?.output_file_id" #footer>
      <a :href="fileApi.downloadUrl(run.output_file_id)" class="btn-ink"><Download class="size-4" />下载这次的结果</a>
    </template>

    <StepDetail
      v-if="selected"
      :step-id="selected.step_id"
      :name="selected.name"
      :formula-name="selected.formula_name"
      :formula-kind="selected.formula_kind"
      :description="selected.description"
      :status="selected.status"
      :report="selected"
      :params="selected.params"
      :run-id="hasTables ? runId : null"
      :error="selected.error"
      :can-rerun="hasTables && !!run?.recipe_id && !rerunning"
      @close="selected = null"
      @rerun="(params) => rerun(selected!, params)"
    />
  </BaseModal>
</template>

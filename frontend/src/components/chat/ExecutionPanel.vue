<script setup lang="ts">
/**
 * 右侧「执行明细」：
 * - 过程：选中那条回答的执行过程（流程每一步的状态、工具调用的参数和结果）
 * - 数据：会话工作区里的表（原始 / 期望结果 / 分析结果 / 流程产出），可以查看、标记为期望结果
 * - 文件：上传的和生成的文件
 * - 流程：这次对话生成的处理流程
 */
import { Download, Eye, FileCheck2, FileSpreadsheet, PanelRightClose, Trash2, Workflow } from '@lucide/vue'
import { computed, ref } from 'vue'

import { conversationApi } from '@/api'
import { errorText } from '@/api/http'
import type { ConversationDetail, TableBrief } from '@/api/types'
import Mascot from '@/components/brand/Mascot.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import RecipeFlow from '@/components/recipe/RecipeFlow.vue'
import StepDetail from '@/components/recipe/StepDetail.vue'
import VerifyBadge from '@/components/recipe/VerifyBadge.vue'
import type { RecipeProgress, StepState, UiMessage } from '@/composables/chatModel'
import { useToast } from '@/composables/useToast'

import TimelineList from './TimelineList.vue'

const props = defineProps<{ detail: ConversationDetail | null; message: UiMessage | null }>()
const emit = defineEmits<{ close: []; openTable: [name: string]; refresh: [] }>()
const toast = useToast()

type Tab = 'process' | 'data' | 'files' | 'recipes'
const tab = ref<Tab>('process')
const selected = ref<{ progress: RecipeProgress; step: StepState } | null>(null)

const progresses = computed(() =>
  (props.message?.artifacts ?? []).filter((a) => a.kind === 'recipe').map((a) => (a as { progress: RecipeProgress }).progress),
)

const GROUPS: { role: TableBrief['role']; label: string }[] = [
  { role: 'input', label: '原始数据' },
  { role: 'expected', label: '期望结果（人工样例）' },
  { role: 'result', label: '流程产出' },
  { role: 'derived', label: '分析结果' },
]
const tableGroups = computed(() =>
  GROUPS.map((g) => ({ ...g, tables: (props.detail?.tables ?? []).filter((t) => t.role === g.role) })).filter((g) => g.tables.length),
)

const files = computed(() => props.detail?.files ?? [])

async function toggleRole(t: TableBrief) {
  if (!props.detail) return
  try {
    await conversationApi.setTableRole(props.detail.id, t.name, t.role === 'expected' ? 'input' : 'expected')
    emit('refresh')
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function drop(t: TableBrief) {
  if (!props.detail || !window.confirm(`删除表「${t.name}」？`)) return
  try {
    await conversationApi.dropTable(props.detail.id, t.name)
    emit('refresh')
  } catch (err) {
    toast.error(errorText(err))
  }
}

function stepFlow(r: ConversationDetail['recipes'][number]) {
  return {
    inputs: r.input_patterns.map((i) => ({ alias: i.alias, description: i.description, file_pattern: i.file_pattern })),
    steps: r.steps.map((s) => ({ id: s.id, name: s.name, action: '', formula: s.formula, formula_kind: s.formula_kind as 'builtin' | 'customer', description: s.description, output: '', inputs: [] })),
    outputs: r.outputs.map((o) => ({ name: o, source: '' })),
  }
}
</script>

<template>
  <aside class="flex h-full w-[392px] shrink-0 flex-col border-l border-line bg-white">
    <div class="flex h-14 items-center justify-between border-b border-line px-4">
      <span class="text-sm font-semibold">执行明细</span>
      <button class="icon-btn" title="收起" @click="emit('close')"><PanelRightClose class="size-4" /></button>
    </div>
    <div class="flex gap-1 border-b border-line px-3 py-2 text-[13px]">
      <button
        v-for="t in ([['process', '过程'], ['data', '数据'], ['files', '文件'], ['recipes', '流程']] as [Tab, string][])"
        :key="t[0]"
        class="rounded-full px-3 py-1 transition"
        :class="tab === t[0] ? 'bg-ink text-white' : 'text-mute hover:bg-paper hover:text-ink'"
        @click="tab = t[0]"
      >
        {{ t[1] }}
        <span v-if="t[0] === 'data' && detail?.tables.length" class="ml-0.5 opacity-60">{{ detail.tables.length }}</span>
      </button>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto p-4">
      <!-- 过程 -->
      <template v-if="tab === 'process'">
        <div v-if="!message || (!message.timeline.length && !progresses.length)" class="flex flex-col items-center py-14 text-center">
          <Mascot :size="72" mood="idle" />
          <p class="mt-3 text-sm text-mute">发送一条消息后，这里会显示<br />每一步做了什么、用了哪个公式、结果怎样</p>
        </div>
        <div v-else class="space-y-5">
          <div v-for="p in progresses" :key="p.key" class="space-y-3">
            <div class="flex items-center gap-2">
              <Workflow class="size-4" />
              <span class="truncate text-[13px] font-semibold">{{ p.recipeName || '处理流程' }}</span>
              <span v-if="p.round > 1" class="tag">第 {{ p.round }} 轮</span>
            </div>
            <RecipeFlow
              v-if="p.flow || p.steps.length"
              direction="vertical"
              :flow="p.flow ?? { inputs: [], outputs: [], steps: p.steps.map((s) => ({ id: s.step_id, name: s.name, action: '', formula: s.formula, formula_kind: s.formula_kind as 'builtin' | 'customer', description: s.description, output: s.output, inputs: [] })) }"
              :states="p.steps"
              @select="(id) => { const s = p.steps.find((x) => x.step_id === id); if (s) selected = { progress: p, step: s } }"
            />
            <VerifyBadge v-if="p.verify" :report="p.verify" :rounds="p.verifyHistory.length" />
            <p v-if="p.validation" class="text-xs text-mute">运行校验：{{ p.validation.summary }}</p>
          </div>
          <div>
            <div class="label-mini mb-2">工具调用</div>
            <TimelineList :items="message.timeline" detailed />
          </div>
        </div>
      </template>

      <!-- 数据 -->
      <template v-else-if="tab === 'data'">
        <div v-if="!tableGroups.length" class="py-14 text-center text-sm text-faint">还没有表，上传 Excel 后会出现在这里</div>
        <div v-for="g in tableGroups" :key="g.role" class="mb-5">
          <div class="label-mini mb-2">{{ g.label }}</div>
          <div v-for="t in g.tables" :key="t.name" class="group mb-1.5 rounded-xl border border-line px-3 py-2.5 transition hover:border-ink/20">
            <div class="flex items-center gap-2">
              <FileCheck2 v-if="t.role === 'expected'" class="size-4 shrink-0 text-[#b7860b]" />
              <FileSpreadsheet v-else class="size-4 shrink-0 text-fun-green" />
              <span class="min-w-0 flex-1 truncate font-mono text-[13px] font-medium">{{ t.name }}</span>
              <span class="shrink-0 font-mono text-[11px] text-faint">{{ t.rows }}×{{ t.cols }}</span>
            </div>
            <div class="mt-1 truncate text-[11px] text-faint">{{ t.label }}</div>
            <div class="mt-2 flex gap-1 opacity-70 transition group-hover:opacity-100">
              <button class="btn-text px-1.5 text-xs" @click="emit('openTable', t.name)"><Eye class="size-3.5" />查看</button>
              <button v-if="t.source === 'uploaded'" class="btn-text px-1.5 text-xs" @click="toggleRole(t)">
                {{ t.role === 'expected' ? '改为原始数据' : '标记为期望结果' }}
              </button>
              <button v-if="t.source === 'computed'" class="btn-text px-1.5 text-xs hover:!text-error" @click="drop(t)"><Trash2 class="size-3.5" />删除</button>
            </div>
          </div>
        </div>
      </template>

      <!-- 文件 -->
      <template v-else-if="tab === 'files'">
        <div v-if="!files.length" class="py-14 text-center text-sm text-faint">还没有文件</div>
        <div v-for="f in files" :key="f.file_id" class="mb-1.5 flex items-center gap-2.5 rounded-xl border border-line px-3 py-2.5">
          <FileCheck2 v-if="f.kind === 'expected'" class="size-4 shrink-0 text-[#b7860b]" />
          <FileSpreadsheet v-else class="size-4 shrink-0" :class="f.kind === 'output' ? 'text-ink' : 'text-fun-green'" />
          <div class="min-w-0 flex-1">
            <div class="truncate text-[13px] font-medium">{{ f.filename }}</div>
            <div class="text-[11px] text-faint">{{ f.kind_label }} · {{ f.sheets.map((s) => `${s.sheet} ${s.rows} 行`).join(' / ') }}</div>
          </div>
          <a :href="f.download_url" class="icon-btn" title="下载"><Download class="size-4" /></a>
        </div>
      </template>

      <!-- 流程 -->
      <template v-else>
        <div v-if="!detail?.recipes.length" class="py-14 text-center text-sm leading-relaxed text-faint">
          这次对话还没有生成流程<br />直接告诉 AI 要怎么整理表格，<br />或在统计后说「保存成流程」
        </div>
        <div v-for="r in detail?.recipes ?? []" :key="r.recipe_id" class="mb-4 rounded-2xl border border-line p-3">
          <div class="flex items-center gap-2">
            <Workflow class="size-4" />
            <span class="min-w-0 flex-1 truncate text-[13px] font-semibold">{{ r.name }}</span>
            <span class="tag">v{{ r.version }}</span>
            <StatusPill :status="r.status" />
          </div>
          <p class="mt-1 line-clamp-2 text-xs text-mute">{{ r.description }}</p>
          <div class="mt-3"><RecipeFlow direction="vertical" :flow="stepFlow(r)" /></div>
          <RouterLink v-if="r.customer_id" :to="{ name: 'customer', params: { id: r.customer_id }, query: { tab: 'recipes' } }" class="btn-text -ml-2 mt-1 text-xs">在客户页查看与重复执行 →</RouterLink>
        </div>
      </template>
    </div>

    <StepDetail
      v-if="selected"
      :step-id="selected.step.step_id"
      :name="selected.step.name"
      :formula-name="selected.step.formula_name"
      :formula-kind="selected.step.formula_kind"
      :description="selected.step.description"
      :status="selected.step.status"
      :report="selected.step.report"
      :run-id="selected.progress.runId"
      :error="selected.step.error"
      :repairs="selected.step.repairs"
      @close="selected = null"
    />
  </aside>
</template>

<script setup lang="ts">
/**
 * 流程概览：说明 → 输入文件 → 处理节点 → 输出文件 → 核对规则 → 列名对应。
 * 每个模块 = 卡片外的大标题 + 一张卡片。流程详情页和版本弹窗共用。
 * 传了 files：文件按流程图里的文件节点去匹配，能查看、下载；不传（查看历史版本）：只列文件名。
 */
import { Download, Eye, FileSpreadsheet, ShieldCheck } from '@lucide/vue'
import { computed } from 'vue'

import type { FileInfo, RecipeFlowData, RecipeJSON } from '@/api/types'
import type { StepState } from '@/composables/chatModel'
import { executionFileSize } from '@/composables/executionPresentation'
import { flowInputFiles, flowOutputFiles } from '@/composables/recipeFlow'
import { inspectorFileMatches } from '@/composables/recipeFlowInspector'
import type { FlowFileNode } from '@/composables/recipeFlowInteraction'
import { recipeStepTitle, recipeToolLabel } from '@/composables/recipeTools'
import { useFilePreview } from '@/composables/useFilePreview'

const props = withDefaults(defineProps<{
  recipe: RecipeJSON
  flow: RecipeFlowData
  files?: FileInfo[]
  states?: StepState[]
  /** 弹窗里用：标题和间距小一号 */
  compact?: boolean
}>(), { files: undefined, states: () => [], compact: false })

const openFilePreview = useFilePreview()
const linked = computed(() => props.files !== undefined)
const steps = computed(() => props.recipe.steps.filter((step) => step.enabled !== false))
const fieldMapping = computed(() => Object.entries(props.recipe.field_mapping ?? {}))

const newest = (list: FileInfo[]) => [...list].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''))[0] ?? null
const fileMeta = (file: FileInfo) => [file.sheets.length ? `${file.sheets.length} 张表` : '', file.size > 0 ? executionFileSize(file.size) : '']
  .filter(Boolean).join(' · ')
const match = (node: FlowFileNode) => (props.files ? newest(inspectorFileMatches(node, props.flow, props.files, props.states)) : null)

const inputFiles = computed(() => flowInputFiles(props.flow).map((item, index) => {
  const node: FlowFileNode = { key: `input:${index}`, direction: 'input', name: item.name, aliases: [...item.aliases], pattern: item.file_pattern, fileId: item.file_id }
  return { node, file: match(node), hint: linked.value ? '还没有文件' : '' }
}))
const outputFiles = computed(() => flowOutputFiles(props.flow).map((item, index) => {
  const node: FlowFileNode = { key: `output:${index}`, direction: 'output', name: item.name, aliases: [], source: item.source, sources: [...(item.sources ?? [])], fileId: item.file_id }
  const sheets = item.sources?.length || (item.source ? 1 : 0)
  return { node, file: match(node), hint: linked.value ? '运行后生成' : sheets ? `${sheets} 张表` : '' }
}))
</script>

<template>
  <div class="overview" :class="{ 'is-compact': compact }">
    <section v-if="recipe.description" class="mod">
      <h2 class="mod-title">说明</h2>
      <div class="mod-card !py-4"><p class="break-words text-[14.5px] leading-[1.85] text-[#2a2a38]">{{ recipe.description }}</p></div>
    </section>

    <section v-if="inputFiles.length" class="mod">
      <h2 class="mod-title">输入文件<small>{{ inputFiles.length }}</small></h2>
      <ul class="mod-card mod-rows">
        <li v-for="item in inputFiles" :key="item.node.key" class="file-row">
          <FileSpreadsheet class="size-5 shrink-0 text-ink" />
          <div class="min-w-0 flex-1">
            <button v-if="item.file" type="button" class="file-name" :title="`查看 ${item.file.filename}`" @click="openFilePreview(item.file)">{{ item.node.name }}</button>
            <span v-else class="file-name" :class="{ 'is-missing': linked }">{{ item.node.name }}</span>
            <p v-if="item.file || item.hint" class="mt-0.5 text-xs text-faint">{{ item.file ? fileMeta(item.file) : item.hint }}</p>
          </div>
          <span v-if="item.file" class="flex shrink-0 items-center">
            <button type="button" class="icon-btn" title="查看" :aria-label="`查看 ${item.file.filename}`" @click="openFilePreview(item.file)"><Eye class="size-4" /></button>
            <a :href="item.file.download_url" class="icon-btn" title="下载" :aria-label="`下载 ${item.file.filename}`"><Download class="size-4" /></a>
          </span>
        </li>
      </ul>
    </section>

    <section v-if="steps.length" class="mod">
      <h2 class="mod-title">处理节点<small>{{ steps.length }}</small></h2>
      <ol class="mod-card mod-rows">
        <li v-for="(step, i) in steps" :key="step.id" class="flex gap-3">
          <span class="step-no">{{ i + 1 }}</span>
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="text-sm font-semibold leading-6" :title="step.name">{{ recipeStepTitle(step) }}</span>
              <span class="kind-tag">{{ recipeToolLabel(step) }}</span>
            </div>
            <p v-if="step.description" class="mt-1 break-words text-[13px] leading-6 text-mute">{{ step.description }}</p>
          </div>
        </li>
      </ol>
    </section>

    <section v-if="outputFiles.length" class="mod">
      <h2 class="mod-title">输出文件<small>{{ outputFiles.length }}</small></h2>
      <ul class="mod-card mod-rows">
        <li v-for="item in outputFiles" :key="item.node.key" class="file-row">
          <FileSpreadsheet class="size-5 shrink-0 text-ink" />
          <div class="min-w-0 flex-1">
            <button v-if="item.file" type="button" class="file-name" :title="`查看 ${item.file.filename}`" @click="openFilePreview(item.file)">{{ item.node.name }}</button>
            <span v-else class="file-name" :class="{ 'is-missing': linked }">{{ item.node.name }}</span>
            <p v-if="item.file || item.hint" class="mt-0.5 text-xs text-faint">{{ item.file ? fileMeta(item.file) : item.hint }}</p>
          </div>
          <span v-if="item.file" class="flex shrink-0 items-center">
            <button type="button" class="icon-btn" title="查看" :aria-label="`查看 ${item.file.filename}`" @click="openFilePreview(item.file)"><Eye class="size-4" /></button>
            <a :href="item.file.download_url" class="icon-btn" title="下载" :aria-label="`下载 ${item.file.filename}`"><Download class="size-4" /></a>
          </span>
        </li>
      </ul>
    </section>

    <section v-if="recipe.validation_rules.length" class="mod">
      <h2 class="mod-title">核对规则<small>{{ recipe.validation_rules.length }}</small></h2>
      <ul class="mod-card mod-rows">
        <li v-for="rule in recipe.validation_rules" :key="rule.id" class="flex gap-2.5 text-sm leading-6">
          <ShieldCheck class="mt-1 size-4 shrink-0" :class="rule.severity === 'error' ? 'text-ink' : 'text-faint'" />{{ rule.description || rule.type }}
        </li>
      </ul>
    </section>

    <section v-if="fieldMapping.length" class="mod">
      <h2 class="mod-title">列名对应<small>{{ fieldMapping.length }}</small></h2>
      <ul class="mod-card mod-rows">
        <li v-for="[std, aliases] in fieldMapping" :key="std" class="out-row">
          <span class="text-sm font-semibold leading-6">{{ std }}</span>
          <span class="flex flex-wrap gap-1.5"><span v-for="alias in aliases" :key="alias" class="chip-flat">{{ alias }}</span></span>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.overview { display: flex; flex-direction: column; gap: 40px; }
.overview.is-compact { gap: 28px; }
/* 模块 = 卡片外的大标题 + 一张卡片 */
.mod { min-width: 0; }
.mod-title { display: flex; align-items: baseline; gap: 8px; margin: 0 2px 12px; color: #14141c; font-size: 18px; font-weight: 700; line-height: 1.35; }
.is-compact .mod-title { margin-bottom: 10px; font-size: 16px; }
.mod-title small { color: #a4a4b2; font-size: 13px; font-weight: 500; font-variant-numeric: tabular-nums; }
.mod-card { min-width: 0; margin: 0; border: 1px solid var(--color-line); border-radius: 16px; background: #fff; padding: 6px 20px; list-style: none; }
/* 卡片里一行一项，项之间细线分隔 */
.mod-rows > li { padding: 14px 0; }
.mod-rows > li + li { border-top: 1px solid #f0f0f4; }
/* 名称 | 说明 两列；窄屏上下排 */
.out-row { display: grid; grid-template-columns: 9.5rem minmax(0, 1fr); gap: 4px 16px; }
@media (max-width: 560px) { .out-row { grid-template-columns: minmax(0, 1fr); } }
.step-no { display: inline-flex; width: 24px; height: 24px; flex: none; align-items: center; justify-content: center; border-radius: 9999px; background: #6265dc14; color: #6265dc; font-size: 12px; font-weight: 700; font-variant-numeric: tabular-nums; }
/* 文件行：图标 + 文件名（点击查看）+ 查看 / 下载 */
.file-row { display: flex; align-items: center; gap: 12px; }
.file-name { display: block; max-width: 100%; overflow: hidden; color: #14141c; font-size: 14px; font-weight: 600; line-height: 1.5; text-align: left; text-overflow: ellipsis; white-space: nowrap; text-underline-offset: 4px; }
button.file-name:hover { text-decoration: underline; }
button.file-name:focus-visible { outline: 2px solid #6265dc; outline-offset: 2px; border-radius: 4px; }
.file-name.is-missing { color: #8a8a98; }
.kind-tag { display: inline-flex; flex: none; align-items: center; border-radius: 8px; background: #6265dc1a; padding: 2px 8px; color: #6265dc; font-size: 12px; font-weight: 600; line-height: 1.6; }
.chip-flat { display: inline-block; max-width: 100%; overflow-wrap: anywhere; border-radius: 7px; background: #f4f4f8; padding: 2px 9px; color: #4a4a58; font-size: 12px; line-height: 1.6; }
</style>

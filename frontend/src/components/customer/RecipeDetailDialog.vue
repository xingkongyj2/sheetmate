<script setup lang="ts">
/**
 * 流程详情：一个流程的全部信息收在这里——概览（说明、输入、步骤、输出、核对规则）、运行记录、版本历史，
 * 以及设为主流程、查看流程数据、删除这些低频操作。
 */
import { Braces, Check, LoaderCircle, Pencil, ShieldCheck, Star, Trash2, X } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import { recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerDetail } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import { percent, timeAgo } from '@/composables/chatModel'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

import RecipeRunsList from './RecipeRunsList.vue'

type CustomerRecipe = CustomerDetail['recipes'][number]
type Tab = 'overview' | 'runs' | 'versions'

const props = defineProps<{ recipe: CustomerRecipe; primary?: boolean; settingPrimary?: boolean; refresh?: () => Promise<void> }>()
const emit = defineEmits<{ close: []; changed: []; setPrimary: [recipeId: string] }>()
const toast = useToast()
const { confirmDelete } = useConfirm()

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: '概览' },
  { key: 'runs', label: '运行记录' },
  { key: 'versions', label: '版本历史' },
]
const tab = ref<Tab>('overview')
const showJson = ref(false)
// 流程名称：点铅笔就地改成输入框，回车保存、Esc 取消
const MAX_NAME = 30  // 与后端 MAX_RECIPE_NAME 一致
const renaming = ref(false)
const draftName = ref('')
const savingName = ref(false)
const renameError = ref('')
const displayName = ref(props.recipe.name)
watch(() => props.recipe.name, (name) => { displayName.value = name })

function startRename() {
  draftName.value = displayName.value
  renameError.value = ''
  renaming.value = true
}
async function saveName() {
  const name = draftName.value.trim()
  if (savingName.value) return
  if (!name || name === displayName.value) { renaming.value = false; return }
  savingName.value = true
  renameError.value = ''
  try {
    const result = await recipeApi.rename(props.recipe.recipe_id, name)
    displayName.value = result.name
    renaming.value = false
    if (result.saved) emit('changed')
  } catch (err) {
    renameError.value = errorText(err)
  } finally {
    savingName.value = false
  }
}
const versions = ref<Awaited<ReturnType<typeof recipeApi.get>>['versions'] | null>(null)
const versionError = ref('')

const body = computed(() => props.recipe.recipe)
const steps = computed(() => body.value.steps.filter((step) => step.enabled !== false))
const verification = computed(() => props.recipe.verification)
const kindLabel = (kind?: string) => (kind === 'sql' ? 'SQL' : kind === 'python' ? '脚本' : '公式')

watch(tab, async (next) => {
  if (next !== 'versions' || versions.value) return
  versionError.value = ''
  try {
    versions.value = (await recipeApi.get(props.recipe.recipe_id)).versions
  } catch (err) {
    versionError.value = errorText(err)
  }
})

async function removeRecipe() {
  const recipeId = props.recipe.recipe_id
  const refresh = props.refresh
  if (!await confirmDelete({
    title: '删除这个流程？',
    target: props.recipe.name,
    description: '这个流程及其所有版本都会被删除，文件和运行记录会保留。删除后无法恢复。',
    confirmText: '删除流程',
    action: async () => {
      await recipeApi.remove(recipeId)
      await refresh?.()
    },
  })) return
  toast.success('已删除流程')
  emit('close')
  if (!refresh) emit('changed')
}
</script>

<template>
  <BaseModal width="max-w-3xl" @close="emit('close')">
    <template #title>
      <div class="min-w-0" :class="renaming ? 'w-[min(34rem,calc(100vw-9rem))]' : ''">
      <div class="flex min-w-0 items-center gap-2">
        <form v-if="renaming" class="flex min-w-0 flex-1 items-center gap-1.5" @submit.prevent="saveName">
          <input v-model="draftName" class="input input-sm min-w-0 flex-1 rounded-xl" aria-label="流程名称" :maxlength="MAX_NAME" autofocus @keydown.esc.stop="renaming = false" />
          <span class="shrink-0 text-[11px] tabular-nums" :class="draftName.length >= MAX_NAME ? 'text-[#b7770c]' : 'text-faint'" aria-live="polite">{{ draftName.length }}/{{ MAX_NAME }}</span>
          <button type="submit" class="icon-btn shrink-0 !text-[#6265dc]" :disabled="savingName" aria-label="保存名称" title="保存"><LoaderCircle v-if="savingName" class="size-4 animate-spin" /><Check v-else class="size-4" /></button>
          <button type="button" class="icon-btn shrink-0" aria-label="取消" title="取消" @click="renaming = false"><X class="size-4" /></button>
        </form>
        <template v-else>
          <h3 class="truncate text-base font-bold" :title="displayName">{{ displayName }}</h3>
          <button type="button" class="icon-btn size-7 shrink-0" aria-label="修改流程名称" title="修改名称" @click="startRename"><Pencil class="size-3.5" /></button>
        </template>
        <span v-if="primary && !renaming" class="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full bg-[#6265DC]/8 px-2 py-1 text-[10px] text-[#6265DC]"><Star class="size-3 fill-current" />主流程</span>
      </div>
      <p v-if="renameError" class="mt-1 text-xs text-error" role="alert">{{ renameError }}</p>
      </div>
    </template>

    <div class="mb-6 inline-flex rounded-full bg-black/[0.04] p-1" role="tablist">
      <button
        v-for="item in TABS" :key="item.key" type="button" role="tab" :aria-selected="tab === item.key"
        class="rounded-full px-4 py-1.5 text-[13px] transition"
        :class="tab === item.key ? 'bg-white font-semibold text-ink shadow-[0_2px_8px_rgba(34,34,66,0.08)]' : 'text-mute hover:text-ink'"
        @click="tab = item.key"
      >{{ item.label }}</button>
    </div>

    <!-- 概览 -->
    <div v-if="tab === 'overview'" class="detail">
      <section class="sec">
        <p v-if="recipe.description" class="break-words text-[14.5px] leading-[1.85] text-[#2a2a38]">{{ recipe.description }}</p>
        <dl class="meta">
          <div><dt>版本</dt><dd>v{{ recipe.version }}</dd></div>
          <div><dt>来源</dt><dd>{{ recipe.created_by === 'ai' ? 'AI 生成' : '人工维护' }}</dd></div>
          <div><dt>更新</dt><dd>{{ timeAgo(recipe.updated_at) }}</dd></div>
          <div v-if="verification">
            <dt>{{ verification.informational ? '与原样例' : '与人工结果' }}</dt>
            <dd class="inline-flex items-center gap-1" :class="verification.informational ? 'text-info' : verification.status === 'pass' ? 'text-success' : 'text-[#b7770c]'">
              <ShieldCheck class="size-3.5" />{{ percent(verification.match_rate) }}{{ verification.informational ? ' 相同' : '' }}
            </dd>
          </div>
        </dl>
      </section>

      <section v-if="body.inputs.length" class="sec">
        <h4 class="sec-title">输入资料<small>{{ body.inputs.length }}</small></h4>
        <ul class="space-y-3">
          <li v-for="inp in body.inputs" :key="inp.alias">
            <div class="break-all text-sm font-semibold">{{ inp.source_file || inp.alias }}</div>
            <p class="mt-0.5 break-words text-[13px] leading-6 text-mute">{{ inp.kind === 'file' ? '完整文件，由脚本读取并核对工作簿内容' : inp.required_columns.length ? `需要包含：${inp.required_columns.join('、')}` : '未指定列要求' }}</p>
          </li>
        </ul>
      </section>

      <section v-if="steps.length" class="sec">
        <h4 class="sec-title">处理步骤<small>{{ steps.length }}</small></h4>
        <ol class="space-y-3.5">
          <li v-for="(step, i) in steps" :key="step.id" class="grid grid-cols-[1.75rem_minmax(0,1fr)]">
            <span class="text-sm font-bold leading-6 tabular-nums text-[#6265dc]">{{ i + 1 }}</span>
            <div class="min-w-0">
              <div class="flex items-center gap-2"><span class="text-sm font-semibold leading-6">{{ step.name }}</span><span class="tag">{{ kindLabel(step.kind) }}</span></div>
              <p v-if="step.description" class="break-words text-[13px] leading-6 text-mute">{{ step.description }}</p>
            </div>
          </li>
        </ol>
      </section>

      <section v-if="body.output_spec.length" class="sec">
        <h4 class="sec-title">输出结果<small>{{ body.output_spec.length }}</small></h4>
        <div class="flex flex-wrap gap-1.5"><span v-for="out in body.output_spec" :key="out.name" class="chip">{{ out.name }}</span></div>
      </section>

      <section class="sec">
        <h4 class="sec-title">自动核对</h4>
        <ul class="space-y-1.5 text-[13px] leading-6 text-mute">
          <li v-for="rule in body.validation_rules" :key="rule.id" class="flex gap-2"><ShieldCheck class="mt-1 size-3.5 shrink-0" :class="rule.severity === 'error' ? 'text-ink' : 'text-faint'" />{{ rule.description || rule.type }}</li>
          <li v-if="!body.validation_rules.length">检查结果是否为空、结果表是否齐全</li>
        </ul>
      </section>

      <section v-if="Object.keys(body.field_mapping || {}).length" class="sec">
        <h4 class="sec-title">列名对应</h4>
        <div class="flex flex-wrap gap-1.5"><span v-for="(aliases, std) in body.field_mapping" :key="std" class="chip">{{ std }} ← {{ aliases.join('/') }}</span></div>
      </section>
    </div>

    <!-- 运行记录 -->
    <RecipeRunsList v-else-if="tab === 'runs'" :recipe-id="recipe.recipe_id" :recipe-name="recipe.name" :customer-id="recipe.customer_id" @changed="emit('changed')" />

    <!-- 版本历史 -->
    <div v-else>
      <p v-if="versionError" class="py-10 text-center text-sm text-error" role="alert">{{ versionError }}</p>
      <p v-else-if="!versions" class="py-12 text-center text-sm text-faint">加载中…</p>
      <ol v-else class="divide-y divide-line">
        <li v-for="v in versions" :key="v.version" class="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
          <span class="tag" :class="v.version === recipe.version ? '!bg-[#6265DC]/10 !text-[#6265DC]' : ''">v{{ v.version }}</span>
          <span class="min-w-0 flex-1 truncate text-sm">{{ v.note || '—' }}</span>
          <span v-if="v.version === recipe.version" class="text-[11px] text-[#6265dc]">当前</span>
          <span class="shrink-0 text-xs text-faint">{{ v.steps }} 步 · {{ v.created_at.slice(0, 16).replace('T', ' ') }}</span>
        </li>
      </ol>
    </div>

    <template #footer>
      <button type="button" class="btn-text mr-auto text-xs" @click="showJson = true"><Braces class="size-3.5" />流程数据</button>
      <button v-if="!primary" type="button" class="btn-line" :disabled="settingPrimary" @click="emit('setPrimary', recipe.recipe_id)"><Star class="size-4" />设为主流程</button>
      <button type="button" class="btn-line border-error/20 text-error hover:border-error/40 hover:bg-error/5" @click="removeRecipe"><Trash2 class="size-4" />删除流程</button>
    </template>

    <BaseModal v-if="showJson" :title="`${recipe.name} · 流程数据`" width="max-w-4xl" @close="showJson = false">
      <pre class="max-h-[65vh] overflow-auto rounded-xl bg-paper p-4 font-mono text-xs leading-relaxed">{{ JSON.stringify(recipe.recipe, null, 2) }}</pre>
    </BaseModal>
  </BaseModal>
</template>

<style scoped>
.sec { padding: 22px 0; border-top: 1px solid #ececf1; }
.sec:first-child { padding-top: 0; border-top: 0; }
.sec:last-child { padding-bottom: 0; }
.sec-title { display: flex; align-items: baseline; gap: 8px; margin: 0 0 12px; color: #14141c; font-size: 15px; font-weight: 700; line-height: 24px; }
.sec-title small { color: #a4a4b2; font-size: 12px; font-weight: 500; font-variant-numeric: tabular-nums; }
.meta { display: flex; flex-wrap: wrap; gap: 6px 28px; margin: 14px 0 0; }
.meta dt { color: #a4a4b2; font-size: 11px; }
.meta dd { margin: 1px 0 0; color: #14141c; font-size: 13px; font-weight: 600; }
</style>

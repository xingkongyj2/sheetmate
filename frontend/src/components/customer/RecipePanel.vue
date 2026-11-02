<script setup lang="ts">
/**
 * 客户页里的一个流程：说明、流程图、运行记录、校验规则、版本历史、启用/停用、用新文件执行。
 */
import { Braces, ChevronDown, ClipboardList, History, Info, Play, Power, ShieldCheck, Trash2 } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import { recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerDetail, RecipeStepJSON } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import RecipeRunsDialog from '@/components/customer/RecipeRunsDialog.vue'
import RecipeFlow from '@/components/recipe/RecipeFlow.vue'
import { percent, timeAgo } from '@/composables/chatModel'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

type CustomerRecipe = CustomerDetail['recipes'][number]

const props = defineProps<{ recipe: CustomerRecipe }>()
const emit = defineEmits<{ run: [recipeId: string]; changed: [] }>()
const toast = useToast()
const { confirmDelete } = useConfirm()

const step = ref<RecipeStepJSON | null>(null)
const showJson = ref(false)
const showDetails = ref(false)
const showRuns = ref(false)
const versions = ref<Awaited<ReturnType<typeof recipeApi.get>>['versions'] | null>(null)
const displayedStatus = ref(props.recipe.status)
const toggling = ref(false)
const toggleFeedback = ref(0)
const isActive = computed(() => displayedStatus.value === 'active')

watch(() => props.recipe.status, (status) => {
  if (!toggling.value) displayedStatus.value = status
})

async function toggleStatus() {
  if (toggling.value) return
  const previous = displayedStatus.value
  const next = isActive.value ? 'deprecated' : 'active'
  toggling.value = true
  displayedStatus.value = next
  toggleFeedback.value += 1
  try {
    const updated = await recipeApi.setStatus(props.recipe.recipe_id, next)
    displayedStatus.value = updated.status
    toast.success(next === 'active' ? '已启用' : '已停用')
    emit('changed')
  } catch (err) {
    displayedStatus.value = previous
    toast.error(errorText(err))
  } finally {
    toggling.value = false
  }
}

async function showVersions() {
  try {
    versions.value = (await recipeApi.get(props.recipe.recipe_id)).versions
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function removeRecipe() {
  const recipeId = props.recipe.recipe_id
  if (!await confirmDelete({
    title: '删除这个流程？',
    target: props.recipe.name,
    description: '这个流程及其所有版本都会被删除，文件和运行记录会保留。删除后无法恢复。',
    confirmText: '删除流程',
    action: () => recipeApi.remove(recipeId),
  })) return
  showDetails.value = false
  toast.success('已删除流程')
  emit('changed')
}

function openStep(id: number) {
  step.value = props.recipe.recipe.steps.find((s) => s.id === id) ?? null
}
</script>

<template>
  <article class="recipe-panel space-y-3">
    <header class="flex flex-wrap items-center gap-x-3 gap-y-2 px-1">
      <div class="recipe-panel-title flex min-w-0 flex-1 items-center gap-2">
        <h3 class="min-w-0 break-words text-[16px] font-semibold leading-relaxed">{{ recipe.name }}</h3>
        <StatusPill :status="displayedStatus" />
      </div>
      <div class="recipe-panel-actions ml-auto flex max-w-full shrink-0 flex-wrap items-center gap-1">
        <button class="btn-ink mr-1 h-8 gap-1.5 whitespace-nowrap px-3 py-0 text-xs" @click="emit('run', recipe.recipe_id)"><Play class="size-3.5" />用新文件执行</button>
        <button type="button" class="icon-btn" title="处理详情" aria-label="处理详情" @click="showDetails = true"><Info class="size-4" /></button>
        <button type="button" class="icon-btn" title="运行记录" aria-label="运行记录" aria-haspopup="dialog" :aria-expanded="showRuns" @click="showRuns = true"><ClipboardList class="size-4" /></button>
        <button class="icon-btn" title="版本历史" @click="showVersions"><History class="size-4" /></button>
        <button
          type="button"
          role="switch"
          class="recipe-power-toggle relative inline-flex size-8 shrink-0 items-center justify-center rounded-full focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-success/50 disabled:cursor-wait"
          :class="isActive ? 'bg-transparent text-success hover:bg-success/10' : 'bg-transparent text-mute hover:bg-black/[0.07] hover:text-ink'"
          :aria-checked="isActive"
          :aria-busy="toggling"
          :aria-label="isActive ? '停用流程' : '启用流程'"
          :title="toggling ? (isActive ? '正在启用…' : '正在停用…') : (isActive ? '停用流程' : '启用流程')"
          :disabled="toggling"
          @click="toggleStatus"
        >
          <span v-if="toggleFeedback" :key="toggleFeedback" class="recipe-power-feedback pointer-events-none absolute inset-0 rounded-full" aria-hidden="true" />
          <Power :key="toggleFeedback" class="recipe-power-icon relative size-4" :class="{ 'recipe-power-icon-feedback': toggleFeedback > 0 }" />
        </button>
      </div>
    </header>

    <div class="rounded-[24px] bg-white p-4 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.10),0_0_0_1px_rgba(34,34,66,0.035)] sm:p-5">
      <p v-if="recipe.description" class="mb-5 break-words text-[13px] leading-[1.8] text-mute">{{ recipe.description }}</p>
      <RecipeFlow :flow="recipe.flow" @select="openStep" />
    </div>

    <RecipeRunsDialog v-if="showRuns" :recipe-id="recipe.recipe_id" :recipe-name="recipe.name" :customer-id="recipe.customer_id" @close="showRuns = false" @changed="emit('changed')" />

    <BaseModal v-if="showDetails" :title="`${recipe.name} · 处理详情`" width="max-w-lg" @close="showDetails = false">
      <div class="space-y-4">
        <div class="flex flex-wrap items-center gap-3 text-xs text-faint">
          <span>更新于 {{ timeAgo(recipe.updated_at) }}</span>
          <span v-if="recipe.verification?.informational" class="flex items-center gap-1 text-info">
            <ShieldCheck class="size-3.5" />已按要求修改 · 与原样例 {{ percent(recipe.verification.match_rate) }} 相同
          </span>
          <span v-else-if="recipe.verification" class="flex items-center gap-1" :class="recipe.verification.status === 'pass' ? 'text-success' : 'text-[#b7770c]'">
            <ShieldCheck class="size-3.5" />与人工结果比对 {{ percent(recipe.verification.match_rate) }}
          </span>
        </div>
        <div class="grid gap-4">
          <div>
            <div class="label-mini mb-2">资料要求</div>
            <div v-for="inp in recipe.recipe.inputs" :key="inp.alias" class="mb-2 rounded-xl bg-paper px-3 py-2 text-xs">
              <div class="flex flex-wrap items-center gap-2"><span class="font-medium text-ink">{{ inp.alias }}</span><span class="break-all font-mono text-faint">{{ inp.file_pattern }}</span></div>
              <div v-if="inp.kind === 'file'" class="mt-1 text-mute">完整文件，由脚本读取并核对工作簿内容。</div>
              <div v-else class="mt-1 break-words text-mute">需要包含：{{ inp.required_columns.join('、') || '未指定列要求' }}</div>
              <div v-if="inp.key_columns.length" class="break-words text-mute">用于匹配的列：{{ inp.key_columns.join('、') }}</div>
            </div>
          </div>
          <div>
            <div class="label-mini mb-2">自动核对</div>
            <ul class="space-y-1 text-xs text-mute">
              <li v-for="rule in recipe.recipe.validation_rules" :key="rule.id" class="flex gap-2">
                <ShieldCheck class="mt-0.5 size-3.5 shrink-0" :class="rule.severity === 'error' ? 'text-ink' : 'text-faint'" />
                {{ rule.description || rule.type }}
              </li>
              <li v-if="!recipe.recipe.validation_rules.length">检查结果是否为空、结果表是否齐全。</li>
            </ul>
            <div v-if="Object.keys(recipe.recipe.field_mapping || {}).length" class="mt-3">
              <div class="label-mini mb-1">不同表格里的列名对应</div>
              <div class="flex flex-wrap gap-1">
                <span v-for="(aliases, std) in recipe.recipe.field_mapping" :key="std" class="chip">{{ std }} ← {{ aliases.join('/') }}</span>
              </div>
            </div>
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-3 text-[11px] text-faint">
          <span>版本 v{{ recipe.version }}</span>
          <span>{{ recipe.created_by === 'ai' ? 'AI 生成' : '人工维护' }}</span>
          <button class="btn-text ml-auto text-xs" title="查看 JSON" @click="showJson = true"><Braces class="size-3.5" />查看流程数据</button>
        </div>
      </div>
      <template #footer>
        <button type="button" class="btn-line w-full justify-center border-error/20 text-error hover:border-error/40 hover:bg-error/5" @click="removeRecipe"><Trash2 class="size-4" />删除流程</button>
      </template>
    </BaseModal>

    <BaseModal v-if="step" :title="step.name" width="max-w-lg" @close="step = null">
      <div class="space-y-4 text-sm">
        <p class="leading-7 text-ink">{{ step.description || '暂无这一步的说明。' }}</p>
        <div v-if="step.explanation" class="rounded-2xl bg-[#f5f5ff] px-4 py-3 text-[13px] leading-6 text-mute">
          <div class="mb-1 font-medium text-ink">处理依据</div>{{ step.explanation }}
        </div>
        <details class="group rounded-2xl bg-paper/70">
          <summary class="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-xs text-mute [&::-webkit-details-marker]:hidden">
            查看详细设置<ChevronDown class="ml-auto size-3.5 transition-transform group-open:rotate-180" />
          </summary>
          <div class="space-y-3 px-4 pb-4 text-xs text-mute">
            <p>处理方式：{{ step.formula }}</p>
            <pre class="max-h-52 overflow-auto rounded-xl bg-white p-3 font-mono leading-relaxed">{{ JSON.stringify(step.params, null, 2) }}</pre>
            <pre v-if="step.sql" class="max-h-52 overflow-auto whitespace-pre-wrap rounded-xl bg-white p-3 font-mono leading-relaxed">{{ step.sql }}</pre>
          </div>
        </details>
      </div>
    </BaseModal>

    <BaseModal v-if="showJson" :title="`${recipe.name} · Recipe JSON`" width="max-w-4xl" @close="showJson = false">
      <pre class="max-h-[65vh] overflow-auto rounded-xl bg-paper p-4 font-mono text-xs leading-relaxed">{{ JSON.stringify(recipe.recipe, null, 2) }}</pre>
    </BaseModal>

    <BaseModal v-if="versions" title="版本历史" width="max-w-lg" @close="versions = null">
      <ol class="space-y-2">
        <li v-for="v in versions" :key="v.version" class="flex items-center gap-3 rounded-xl border border-line px-3 py-2 text-sm">
          <span class="tag">v{{ v.version }}</span>
          <span class="min-w-0 flex-1 truncate">{{ v.note || '—' }}</span>
          <span class="text-xs text-faint">{{ v.steps }} 步 · {{ v.created_at.slice(0, 16).replace('T', ' ') }}</span>
          <StatusPill :status="v.status" />
        </li>
      </ol>
    </BaseModal>
  </article>
</template>

<style scoped>
.recipe-panel { container-type: inline-size; }
.recipe-power-toggle { transition: color .24s ease, background-color .24s ease, transform .18s ease; }
.recipe-power-toggle:active { transform: scale(.9); }
.recipe-power-feedback { box-shadow: 0 0 0 2px currentColor; animation: recipe-power-ripple .55s ease-out both; }
.recipe-power-icon-feedback { animation: recipe-power-pop .38s ease-out; }
@keyframes recipe-power-ripple {
  from { opacity: .32; transform: scale(.8); }
  to { opacity: 0; transform: scale(1.5); }
}
@keyframes recipe-power-pop {
  0% { transform: scale(.8); }
  55% { transform: scale(1.15); }
  100% { transform: scale(1); }
}
@media (prefers-reduced-motion: reduce) {
  .recipe-power-toggle { transition: none; }
  .recipe-power-toggle:active { transform: none; }
  .recipe-power-feedback { animation: none; opacity: 0; }
  .recipe-power-icon-feedback { animation: none; }
}
@container (max-width: 540px) {
  .recipe-panel-title { flex-basis: 100%; }
  .recipe-panel-actions { margin-left: 0; }
}
</style>

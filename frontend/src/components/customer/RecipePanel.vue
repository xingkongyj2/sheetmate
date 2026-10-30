<script setup lang="ts">
/**
 * 客户页里的一个流程：说明、输入要求、流程图、输出、校验规则、版本历史、启用/停用、查看 JSON、用新文件执行。
 */
import { Braces, History, Play, Power, ShieldCheck } from '@lucide/vue'
import { ref } from 'vue'

import { recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerDetail, RecipeStepJSON } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import RecipeFlow from '@/components/recipe/RecipeFlow.vue'
import StepDetail from '@/components/recipe/StepDetail.vue'
import { percent, timeAgo } from '@/composables/chatModel'
import { useToast } from '@/composables/useToast'

type CustomerRecipe = CustomerDetail['recipes'][number]

const props = defineProps<{ recipe: CustomerRecipe }>()
const emit = defineEmits<{ run: [recipeId: string]; changed: [] }>()
const toast = useToast()

const step = ref<RecipeStepJSON | null>(null)
const showJson = ref(false)
const versions = ref<Awaited<ReturnType<typeof recipeApi.get>>['versions'] | null>(null)

async function toggleStatus() {
  const next = props.recipe.status === 'active' ? 'deprecated' : 'active'
  try {
    await recipeApi.setStatus(props.recipe.recipe_id, next)
    toast.success(next === 'active' ? '已启用' : '已停用')
    emit('changed')
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function showVersions() {
  try {
    versions.value = (await recipeApi.get(props.recipe.recipe_id)).versions
  } catch (err) {
    toast.error(errorText(err))
  }
}

function openStep(id: number) {
  step.value = props.recipe.recipe.steps.find((s) => s.id === id) ?? null
}
</script>

<template>
  <article class="card-soft overflow-hidden">
    <div class="flex flex-wrap items-start gap-3 border-b border-line px-5 py-4">
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2">
          <h3 class="text-[16px] font-semibold">{{ recipe.name }}</h3>
          <span class="tag">v{{ recipe.version }}</span>
          <StatusPill :status="recipe.status" />
        </div>
        <p class="mt-1 text-[13px] leading-relaxed text-mute">{{ recipe.description }}</p>
        <div class="mt-2 flex flex-wrap items-center gap-3 text-xs text-faint">
          <span v-if="recipe.verification?.informational" class="flex items-center gap-1 text-info">
            <ShieldCheck class="size-3.5" />已按要求修改 · 与原样例 {{ percent(recipe.verification.match_rate) }} 相同
          </span>
          <span v-else-if="recipe.verification" class="flex items-center gap-1" :class="recipe.verification.status === 'pass' ? 'text-success' : 'text-[#b7770c]'">
            <ShieldCheck class="size-3.5" />与人工结果比对 {{ percent(recipe.verification.match_rate) }}
          </span>
          <span>{{ recipe.created_by === 'ai' ? 'AI 生成' : '人工维护' }}</span>
          <span>更新于 {{ timeAgo(recipe.updated_at) }}</span>
        </div>
      </div>
      <div class="flex items-center gap-1.5">
        <button class="btn-ink py-2" @click="emit('run', recipe.recipe_id)"><Play class="size-4" />用新文件执行</button>
        <button class="icon-btn" title="版本历史" @click="showVersions"><History class="size-4" /></button>
        <button class="icon-btn" title="查看 JSON" @click="showJson = true"><Braces class="size-4" /></button>
        <button class="icon-btn" :title="recipe.status === 'active' ? '停用' : '启用'" @click="toggleStatus"><Power class="size-4" /></button>
      </div>
    </div>

    <div class="space-y-4 px-5 py-4">
      <RecipeFlow :flow="recipe.flow" @select="openStep" />

      <div class="grid gap-4 md:grid-cols-2">
        <div>
          <div class="label-mini mb-2">输入要求</div>
          <div v-for="inp in recipe.recipe.inputs" :key="inp.alias" class="mb-2 rounded-xl bg-paper px-3 py-2 text-xs">
            <div class="flex items-center gap-2"><span class="font-medium text-ink">{{ inp.alias }}</span><span class="font-mono text-faint">{{ inp.file_pattern }}</span></div>
            <div class="mt-1 text-mute">必需列：{{ inp.required_columns.join('、') || '无' }}</div>
            <div v-if="inp.key_columns.length" class="text-mute">主键：{{ inp.key_columns.join('、') }}</div>
          </div>
        </div>
        <div>
          <div class="label-mini mb-2">校验规则（每次执行都会检查）</div>
          <ul class="space-y-1 text-xs text-mute">
            <li v-for="rule in recipe.recipe.validation_rules" :key="rule.id" class="flex gap-2">
              <ShieldCheck class="mt-0.5 size-3.5 shrink-0" :class="rule.severity === 'error' ? 'text-ink' : 'text-faint'" />
              {{ rule.description || rule.type }}
            </li>
            <li v-if="!recipe.recipe.validation_rules.length">（只有自动检查：输出不为空、sheet 齐全等）</li>
          </ul>
          <div v-if="Object.keys(recipe.recipe.field_mapping || {}).length" class="mt-3">
            <div class="label-mini mb-1">依赖的字段映射</div>
            <div class="flex flex-wrap gap-1">
              <span v-for="(aliases, std) in recipe.recipe.field_mapping" :key="std" class="chip">{{ std }} ← {{ aliases.join('/') }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <StepDetail
      v-if="step"
      :step-id="step.id"
      :name="step.name"
      :formula-name="step.formula"
      :formula-kind="step.formula_kind"
      :description="step.description"
      :explanation="step.explanation"
      :params="step.params"
      :report="step.sql ? ({ sql: step.sql, params: step.params } as never) : null"
      @close="step = null"
    />

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

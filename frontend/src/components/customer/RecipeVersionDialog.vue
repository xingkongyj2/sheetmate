<script setup lang="ts">
/**
 * 版本详情弹窗：这个版本怎么来的（修改说明、来源、时间、运行次数、来源对话、修改要求），
 * 两个标签——「改动」：和上一版相比改了什么（脚本、SQL 给出逐行差异）；「完整内容」：这个版本的概览。
 * 第一版没有上一版，只显示完整内容。
 */
import { ChevronRight, LoaderCircle } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { recipeApi } from '@/api'
import { errorText } from '@/api/http'
import type { RecipeFlowData, RecipeJSON, RecipeVersion, RecipeVersionChange, RecipeVersionChanges } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'
import RecipeOverview from '@/components/recipe/RecipeOverview.vue'

const props = defineProps<{ recipeId: string; item: RecipeVersion }>()
const emit = defineEmits<{ close: [] }>()

const recipe = ref<RecipeJSON | null>(null)
const flow = ref<RecipeFlowData | null>(null)
const changes = ref<RecipeVersionChanges | null>(null)
const loadError = ref('')
const hasBase = computed(() => props.item.version > 1)
const tab = ref<'changes' | 'content'>(hasBase.value ? 'changes' : 'content')
const tabs = computed(() => [
  { key: 'changes' as const, label: `改动（与 v${changes.value?.base_version ?? props.item.version - 1} 相比）` },
  { key: 'content' as const, label: '完整内容' },
])

const savedAt = computed(() => props.item.created_at.slice(0, 16).replace('T', ' '))
const source = computed(() => (props.item.created_by === 'human' ? '人工修改' : props.item.version <= 1 ? 'AI 生成' : 'AI 修订'))
const requirement = computed(() => {
  const value = recipe.value?.metadata?.requirement
  return typeof value === 'string' ? value.trim() : ''
})

const KIND: Record<RecipeVersionChange['kind'], { label: string; cls: string }> = {
  added: { label: '新增', cls: 'is-added' },
  removed: { label: '删除', cls: 'is-removed' },
  modified: { label: '修改', cls: 'is-modified' },
}
const codeLabel = (language: string) => (language === 'sql' ? 'SQL' : 'Python 脚本')
const lineClass = (line: string) => (line.startsWith('+') ? 'is-add' : line.startsWith('-') ? 'is-del' : line.startsWith('@@') ? 'is-hunk' : '')

onMounted(async () => {
  try {
    const [detail, diff] = await Promise.all([
      recipeApi.get(props.recipeId, props.item.version),
      hasBase.value ? recipeApi.changes(props.recipeId, props.item.version) : Promise.resolve(null),
    ])
    recipe.value = detail.recipe
    flow.value = detail.flow
    changes.value = diff
  } catch (err) {
    loadError.value = errorText(err)
  }
})
</script>

<template>
  <BaseModal width="max-w-2xl" @close="emit('close')">
    <template #title>
      <div class="flex items-center gap-2">
        <span class="rounded-md px-1.5 py-0.5 text-xs font-semibold" :class="item.latest ? 'bg-ink text-white' : 'bg-paper text-mute'">v{{ item.version }}</span>
        <h3 class="text-base font-semibold">版本详情</h3>
        <span v-if="item.latest" class="text-xs text-[#6265dc]">当前</span>
      </div>
    </template>

    <!-- 这个版本是怎么来的 -->
    <div class="rounded-2xl bg-paper px-5 py-4">
      <p class="break-words text-[15px] font-semibold leading-relaxed text-ink">{{ item.note || '没有填写修改说明' }}</p>
      <div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-mute">
        <span v-if="source !== item.note">{{ source }}</span>
        <span class="tabular-nums">{{ savedAt }}</span>
        <span v-if="item.runs !== undefined">运行 {{ item.runs }} 次</span>
        <RouterLink
          v-if="item.conversation_id" :to="{ name: 'chat', params: { id: item.conversation_id } }"
          class="inline-flex items-center gap-0.5 hover:text-ink hover:underline underline-offset-4"
        >来源对话<ChevronRight class="size-3.5" /></RouterLink>
      </div>
      <details v-if="requirement" class="fold mt-3">
        <summary><ChevronRight class="size-3.5" />修改要求</summary>
        <p class="mt-2 whitespace-pre-wrap break-words text-[13px] leading-6 text-[#4a4a58]">{{ requirement }}</p>
      </details>
    </div>

    <div v-if="hasBase" class="mt-6 inline-flex rounded-full bg-paper/80 p-1" role="tablist" aria-label="版本详情">
      <button
        v-for="item2 in tabs" :key="item2.key" type="button" role="tab" :aria-selected="tab === item2.key"
        class="rounded-full px-3.5 py-1.5 text-[13px] transition"
        :class="tab === item2.key ? 'bg-white font-medium text-ink shadow-[0_2px_8px_rgba(34,34,66,0.08)]' : 'text-mute hover:text-ink'"
        @click="tab = item2.key"
      >{{ item2.label }}</button>
    </div>

    <p v-if="loadError" class="py-10 text-center text-sm text-error" role="alert">{{ loadError }}</p>
    <div v-else-if="!recipe || !flow" class="flex justify-center py-12"><LoaderCircle class="size-5 animate-spin text-mute" /></div>

    <!-- 改动 -->
    <div v-else-if="tab === 'changes'" class="mt-6">
      <p v-if="!changes?.sections.length" class="rounded-2xl border border-line py-10 text-center text-sm text-faint">和上一版相比，处理内容没有变化</p>
      <div v-else class="space-y-7">
        <section v-for="section in changes.sections" :key="section.key">
          <h4 class="mb-2.5 flex items-baseline gap-2 px-0.5 text-base font-bold">{{ section.title }}<small class="text-[13px] font-medium text-faint">{{ section.items.length }}</small></h4>
          <ul class="change-card">
            <li v-for="(change, index) in section.items" :key="`${change.kind}:${change.title}:${index}`" class="change-row">
              <div class="flex min-w-0 items-start gap-2.5">
                <span class="change-kind" :class="KIND[change.kind].cls">{{ KIND[change.kind].label }}</span>
                <span class="min-w-0 break-words text-sm font-semibold leading-6" :class="{ 'text-faint line-through': change.kind === 'removed' }">{{ change.title }}</span>
              </div>
              <ul v-if="change.details.length" class="mt-1.5 space-y-0.5 pl-[3.25rem] text-[13px] leading-6 text-mute">
                <li v-for="line in change.details" :key="line" class="break-words">{{ line }}</li>
              </ul>
              <!-- 文字改动：修改前 / 修改后；新增的只显示内容 -->
              <div v-if="change.kind === 'modified' && (change.before || change.after)" class="mt-2.5 space-y-2 pl-[3.25rem]">
                <p class="text-block is-before"><b>修改前</b>{{ change.before || '（空）' }}</p>
                <p class="text-block is-after"><b>修改后</b>{{ change.after || '（空）' }}</p>
              </div>
              <p v-else-if="change.after" class="mt-1 break-words pl-[3.25rem] text-[13px] leading-6 text-mute">{{ change.after }}</p>
              <!-- 代码逐行差异：默认收起 -->
              <details v-if="change.code" class="fold mt-2.5 pl-[3.25rem]">
                <summary>
                  <ChevronRight class="size-3.5" />{{ codeLabel(change.code.language) }}改动
                  <span v-if="change.code.added" class="tabular-nums text-success">+{{ change.code.added }}</span><span v-if="change.code.removed" class="tabular-nums text-error">−{{ change.code.removed }}</span>
                </summary>
                <pre class="diff"><span v-for="(line, i) in change.code.diff.split('\n')" :key="i" :class="lineClass(line)">{{ line || ' ' }}</span></pre>
                <p v-if="change.code.truncated" class="mt-1 text-xs text-faint">改动太长，只显示前一部分</p>
              </details>
            </li>
          </ul>
        </section>
      </div>
    </div>

    <!-- 完整内容 -->
    <RecipeOverview v-else class="mt-6" :recipe="recipe" :flow="flow" compact />
  </BaseModal>
</template>

<style scoped>
.fold > summary { display: inline-flex; cursor: pointer; align-items: center; gap: 6px; color: var(--color-mute); font-size: 13px; list-style: none; }
.fold > summary::-webkit-details-marker { display: none; }
.fold > summary:hover { color: var(--color-ink); }
.fold > summary svg { transition: transform .15s; }
.fold[open] > summary svg { transform: rotate(90deg); }

.change-card { margin: 0; border: 1px solid var(--color-line); border-radius: 16px; background: #fff; padding: 4px 18px; list-style: none; }
.change-row { padding: 14px 0; }
.change-row + .change-row { border-top: 1px solid #f0f0f4; }
.change-kind { display: inline-flex; flex: none; align-items: center; border-radius: 7px; padding: 1px 8px; font-size: 12px; font-weight: 600; line-height: 22px; }
.change-kind.is-added { background: #318b761a; color: #318b76; }
.change-kind.is-removed { background: color-mix(in srgb, var(--color-error) 10%, transparent); color: var(--color-error); }
.change-kind.is-modified { background: #b7770c1a; color: #b7770c; }

.text-block { margin: 0; border-radius: 10px; padding: 8px 12px; font-size: 13px; line-height: 1.75; overflow-wrap: anywhere; white-space: pre-wrap; }
.text-block b { display: block; margin-bottom: 2px; font-size: 11px; font-weight: 600; }
.text-block.is-before { background: #f7f7f9; color: #8a8a98; }
.text-block.is-before b { color: #a4a4b2; }
.text-block.is-after { background: #318b760d; color: #2a2a38; }
.text-block.is-after b { color: #318b76; }

.diff { margin: 8px 0 0; max-height: 360px; overflow: auto; border-radius: 10px; background: #fafafc; padding: 8px 0; font: 12px/1.6 var(--font-mono, ui-monospace, monospace); }
.diff span { display: block; padding: 0 12px; white-space: pre; }
.diff .is-add { background: #318b7614; color: #1f6b59; }
.diff .is-del { background: color-mix(in srgb, var(--color-error) 8%, transparent); color: #b42318; }
.diff .is-hunk { color: #a4a4b2; }
</style>

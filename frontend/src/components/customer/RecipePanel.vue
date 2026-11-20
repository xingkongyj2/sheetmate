<script setup lang="ts">
/**
 * 客户页里的一个流程：流程图和说明；头部只有「用新文件执行」和「详情」两个按钮，
 * 「详情」进入流程详情页（运行记录、版本历史、处理详情、设为主流程、删除都在那里）。
 */
import { Play, SlidersHorizontal, Star } from '@lucide/vue'
import { computed } from 'vue'

import type { CustomerDetail, FileInfo } from '@/api/types'
import RecipeFlow from '@/components/recipe/RecipeFlow.vue'
import { inspectorStates } from '@/composables/recipeFlowInspector'

type CustomerRecipe = CustomerDetail['recipes'][number]

const props = defineProps<{
  recipe: CustomerRecipe
  customerId: string
  files?: FileInfo[]
  primary?: boolean
}>()
const emit = defineEmits<{ run: [recipeId: string]; changed: [] }>()

const latestRun = computed(() => props.recipe.runs.find((run) => run.recipe_version === props.recipe.version))
const states = computed(() => inspectorStates([], latestRun.value?.steps ?? []))
</script>

<template>
  <article class="recipe-panel space-y-3">
    <header class="flex flex-wrap items-center gap-x-3 gap-y-2 px-1">
      <div class="recipe-panel-title flex min-w-0 flex-1 items-center gap-2">
        <h3 class="min-w-0 break-words text-[16px] font-semibold leading-relaxed">{{ recipe.name }}</h3>
        <span v-if="primary" class="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full bg-[#6265DC]/8 px-2 py-1 text-[10px] text-[#6265DC]"><Star class="size-3 fill-current" />主流程</span>
      </div>
      <div class="recipe-panel-actions ml-auto flex max-w-full shrink-0 flex-wrap items-center gap-2">
        <button type="button" class="btn-line size-8 justify-center p-0" title="用新文件执行" aria-label="用新文件执行" @click="emit('run', recipe.recipe_id)"><Play class="size-4" /></button>
        <RouterLink :to="{ name: 'recipe', params: { id: customerId, recipeId: recipe.recipe_id } }" class="btn-line size-8 justify-center p-0" title="流程详情" aria-label="流程详情"><SlidersHorizontal class="size-4" /></RouterLink>
      </div>
    </header>

    <div class="rounded-[24px] bg-white p-4 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.10),0_0_0_1px_rgba(34,34,66,0.035)] sm:p-5">
      <RecipeFlow
        toolbar-placement="hidden"
        :flow="recipe.flow"
        :states="states"
        :context="{ title: recipe.name, recipeId: recipe.recipe_id, version: recipe.version, customerId: recipe.customer_id, conversationId: recipe.conversation_id, recipe: recipe.recipe, files, runId: latestRun?.run_id }"
        @changed="emit('changed')"
      />
    </div>
  </article>
</template>

<style scoped>
.recipe-panel { container-type: inline-size; }
@container (max-width: 540px) {
  .recipe-panel-title { flex-basis: 100%; }
  .recipe-panel-actions { margin-left: 0; }
}
</style>

<script setup lang="ts">
import type { FileInfo } from '@/api/types'
import RecipeFlow from '@/components/recipe/RecipeFlow.vue'
import type { RecipeViewArtifact } from '@/composables/chatModel'
import { userFacingSummary } from '@/composables/timelinePresentation'

defineProps<{
  view: RecipeViewArtifact
  files?: FileInfo[]
  customerId?: string | null
  conversationId?: string | null
}>()
const emit = defineEmits<{ refresh: [] }>()
</script>

<template>
  <article class="recipe-view min-w-0 space-y-3" :aria-label="view.name">
    <header class="flex flex-wrap items-center gap-x-3 gap-y-1 px-1">
      <h3 class="min-w-0 break-words text-[16px] font-semibold leading-relaxed">{{ view.name }}</h3>
      <span v-if="view.deleted" class="text-xs text-faint">流程已删除</span>
    </header>
    <div class="min-w-0 rounded-[24px] bg-white p-4 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.10),0_0_0_1px_rgba(34,34,66,0.035)] sm:p-5">
      <p v-if="view.description" class="mb-5 whitespace-pre-line break-words text-[13px] leading-[1.8] text-mute">{{ userFacingSummary(view.description) }}</p>
      <RecipeFlow
        :flow="view.flow"
        :context="{ title: view.name, recipeId: view.recipe_id, version: view.version, customerId, conversationId, files, deleted: view.deleted }"
        @changed="emit('refresh')"
      />
    </div>
  </article>
</template>

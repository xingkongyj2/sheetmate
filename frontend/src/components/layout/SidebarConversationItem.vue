<script setup lang="ts">
/** 侧边栏里的一条对话：单色设计感对话图标 + 标题 + 时间；悬停时时间换成删除按钮。客户下面的对话，图标对齐客户头像、标题对齐客户名。 */
import { LoaderCircle, Trash2 } from '@lucide/vue'

import type { ConversationBrief } from '@/api/types'
import ConversationIcon from '@/components/brand/ConversationIcon.vue'
import { shortTime } from '@/composables/conversationGroups'

defineProps<{ conversation: ConversationBrief; active: boolean; nested?: boolean }>()
const emit = defineEmits<{ remove: [] }>()
</script>

<template>
  <div class="group relative">
    <RouterLink
      :to="{ name: 'chat', params: { id: conversation.id } }"
      class="flex items-center rounded-xl py-2 pr-3 text-sm transition hover:bg-black/[0.04]"
      :class="[nested ? 'gap-4 pl-[17px]' : 'gap-2.5 pl-3', active ? 'bg-black/[0.06] text-ink' : 'text-ink/85']"
      :title="conversation.customer ? `${conversation.title} · ${conversation.customer.name}` : conversation.title"
      :aria-current="active ? 'page' : undefined"
    >
      <LoaderCircle v-if="conversation.running" class="size-4 shrink-0 animate-spin text-info" aria-label="运行中" />
      <ConversationIcon
        v-else
        :active="active"
        class="size-4 shrink-0 transition-colors"
        :class="active ? 'text-ink' : 'text-mute/80 group-hover:text-ink/85'"
      />
      <span class="min-w-0 flex-1 truncate">{{ conversation.title }}</span>
      <span class="shrink-0 text-[11px] text-faint transition group-hover:opacity-0">{{ shortTime(conversation.updated_at) }}</span>
    </RouterLink>
    <button
      type="button"
      class="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1 text-faint opacity-0 transition hover:bg-black/[0.05] hover:text-error focus-visible:opacity-100 group-hover:opacity-100"
      title="删除对话"
      :aria-label="`删除对话 ${conversation.title}`"
      @click.prevent="emit('remove')"
    >
      <Trash2 class="size-3.5" />
    </button>
  </div>
</template>

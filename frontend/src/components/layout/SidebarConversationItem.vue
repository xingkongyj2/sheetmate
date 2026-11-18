<script setup lang="ts">
/** 侧边栏里的一条对话：图标 + 标题 + 时间；绑定了客户的，对话图标按客户颜色染色（客户下面的对话不重复显示）。悬停时时间换成删除按钮。客户下面的对话，图标对齐客户头像、标题对齐客户名。 */
import { LoaderCircle, MessageSquare, Trash2 } from '@lucide/vue'
import { computed } from 'vue'

import type { ConversationBrief } from '@/api/types'
import { mascotStyle } from '@/components/brand/mascots'
import { shortTime } from '@/composables/conversationGroups'

const props = defineProps<{ conversation: ConversationBrief; active: boolean; nested?: boolean }>()
// 客户下面的对话不染色（上面已经有客户头像了）
const tint = computed(() => (props.conversation.customer && !props.nested ? mascotStyle(props.conversation.customer.mascot).body : ''))
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
      <MessageSquare
        v-else
        class="size-4 shrink-0"
        :class="tint ? '' : active ? 'text-ink' : 'text-faint'"
        :style="tint ? { color: tint, fill: `color-mix(in srgb, ${tint} 18%, transparent)` } : undefined"
        aria-hidden="true"
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

<script setup lang="ts">
/**
 * 搜索：按标题、客户名搜对话，按名称、编号搜客户。
 * 数据来自侧边栏已加载的列表，不另外请求；↑↓ 选择，回车打开，Esc 关闭。
 */
import { MessageSquare, Search } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter, type RouteLocationRaw } from 'vue-router'

import { mascotStyle } from '@/components/brand/mascots'
import { useWorkspaceStore } from '@/stores/conversations'

const emit = defineEmits<{ close: [] }>()
const store = useWorkspaceStore()
const router = useRouter()

const query = ref('')
const active = ref(0)
const input = ref<HTMLInputElement | null>(null)
const list = ref<HTMLElement | null>(null)

interface Hit {
  key: string
  kind: 'conversation' | 'customer'
  title: string
  sub: string
  color?: string
  to: RouteLocationRaw
}

const LIMIT = 30

const groups = computed(() => {
  const q = query.value.trim().toLowerCase()
  const has = (...values: (string | undefined | null)[]) => values.some((v) => v?.toLowerCase().includes(q))
  const conversations: Hit[] = store.conversations
    .filter((c) => !q || has(c.title, c.customer?.name))
    .slice(0, q ? LIMIT : 8)
    .map((c) => ({
      key: `c:${c.id}`,
      kind: 'conversation',
      title: c.title,
      sub: c.customer?.name ?? '无客户',
      color: c.customer ? mascotStyle(c.customer.mascot).body : undefined,
      to: { name: 'chat', params: { id: c.id } },
    }))
  const customers: Hit[] = q
    ? store.customers
      .filter((c) => has(c.name, c.code))
      .slice(0, LIMIT)
      .map((c) => ({
        key: `u:${c.id}`,
        kind: 'customer',
        title: c.name,
        sub: c.code,
        color: mascotStyle(c.mascot).body,
        to: { name: 'customer', params: { id: c.id } },
      }))
    : []
  return [
    { label: q ? '对话' : '最近对话', items: conversations },
    { label: '客户', items: customers },
  ].filter((g) => g.items.length)
})
const hits = computed(() => groups.value.flatMap((g) => g.items))

watch(query, () => { active.value = 0 })

function move(step: number) {
  if (!hits.value.length) return
  active.value = (active.value + step + hits.value.length) % hits.value.length
  nextTick(() => list.value?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }))
}

function open(hit: Hit | undefined) {
  if (!hit) return
  emit('close')
  router.push(hit.to)
}

function onKey(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') { event.preventDefault(); move(1) }
  else if (event.key === 'ArrowUp') { event.preventDefault(); move(-1) }
  else if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); open(hits.value[active.value]) }
  else if (event.key === 'Escape') { event.preventDefault(); emit('close') }
}

const previousFocus = document.activeElement as HTMLElement | null
onMounted(() => input.value?.focus())
onBeforeUnmount(() => previousFocus?.focus?.())
</script>

<template>
  <Teleport to="body">
    <div class="fixed inset-0 z-[4500] flex items-start justify-center px-4 pt-[14vh]" role="dialog" aria-modal="true" aria-label="搜索">
      <div class="absolute inset-0 bg-black/20 backdrop-blur-[2px]" @click="emit('close')" />
      <div class="relative flex max-h-[70vh] w-full max-w-[560px] animate-pop flex-col overflow-hidden rounded-[22px] bg-white shadow-[0_24px_60px_-20px_rgba(0,0,0,0.25),0_0_0_1px_rgba(0,0,0,0.04)]">
        <div class="flex items-center gap-2.5 px-4 py-3.5">
          <Search class="size-[18px] shrink-0 text-faint" aria-hidden="true" />
          <input
            ref="input"
            v-model="query"
            type="text"
            class="min-w-0 flex-1 bg-transparent text-[15px] text-ink outline-none placeholder:text-faint focus:outline-none"
            placeholder="搜索对话、客户"
            role="combobox"
            aria-expanded="true"
            aria-controls="search-results"
            :aria-activedescendant="hits[active] ? `search-${hits[active].key}` : undefined"
            @keydown="onKey"
          />
          <kbd class="shrink-0 rounded-md border border-line px-1.5 py-0.5 font-sans text-[11px] text-faint">Esc</kbd>
        </div>

        <div id="search-results" ref="list" class="min-h-0 overflow-y-auto border-t border-line p-1.5" role="listbox" aria-label="搜索结果">
          <template v-for="group in groups" :key="group.label">
            <div class="label-mini px-3 pb-1 pt-2.5">{{ group.label }}</div>
            <div
              v-for="hit in group.items"
              :id="`search-${hit.key}`"
              :key="hit.key"
              role="option"
              :aria-selected="hits[active]?.key === hit.key"
              class="flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2"
              :class="hits[active]?.key === hit.key ? 'bg-black/[0.05]' : ''"
              @mousemove="active = hits.indexOf(hit)"
              @click="open(hit)"
            >
              <MessageSquare v-if="hit.kind === 'conversation'" class="size-4 shrink-0 text-faint" aria-hidden="true" />
              <span v-else class="mx-1 size-2 shrink-0 rounded-full" :style="{ background: hit.color }" aria-hidden="true" />
              <span class="min-w-0 flex-1 truncate text-sm text-ink">{{ hit.title }}</span>
              <span v-if="hit.sub" class="flex max-w-[40%] shrink-0 items-center gap-1.5 truncate text-xs text-faint">
                <span v-if="hit.kind === 'conversation' && hit.color" class="size-1.5 shrink-0 rounded-full" :style="{ background: hit.color }" />
                <span class="truncate">{{ hit.sub }}</span>
              </span>
            </div>
          </template>
          <div v-if="!hits.length" class="px-3 py-8 text-center text-sm text-faint">
            {{ query.trim() ? '没有找到相关内容' : '暂无对话' }}
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

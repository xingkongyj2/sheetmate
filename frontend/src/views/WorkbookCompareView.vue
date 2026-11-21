<script setup lang="ts">
/** 独立表格比对页：整页展示两个表格的差异，不带侧边栏。支持通过 URL query 恢复与刷新。 */
import { computed, defineAsyncComponent, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import MascotLoader from '@/components/brand/MascotLoader.vue'
import type { CompareSource } from '@/features/workbook-compare/types'
import { buildCompareQuery, sourceFromQuery } from '@/features/workbook-compare/url'

const WorkbookCompare = defineAsyncComponent({
  loader: () => import('@/features/workbook-compare/WorkbookCompare.vue'),
  loadingComponent: {
    render: () => h(MascotLoader, { size: 'lg', class: 'h-dvh' }),
  },
})

const route = useRoute()
const router = useRouter()

const initialLeft = computed(() => sourceFromQuery(route.query as Record<string, unknown>, 'left'))
const initialRight = computed(() => sourceFromQuery(route.query as Record<string, unknown>, 'right'))
const conversationId = computed(() => {
  const raw = typeof route.query.conversation === 'string' ? route.query.conversation.trim() : ''
  return raw || null
})

function onUpdateSources(payload: { left: CompareSource | null; right: CompareSource | null; conversationId?: string | null }) {
  const query = buildCompareQuery(payload.left, payload.right, payload.conversationId ?? conversationId.value)
  void router.replace({ name: 'compare', query })
}
</script>

<template>
  <WorkbookCompare
    :initial-left="initialLeft"
    :initial-right="initialRight"
    :conversation-id="conversationId"
    @update-sources="onUpdateSources"
  />
</template>

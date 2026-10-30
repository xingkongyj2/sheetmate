<script setup lang="ts">
/** 对话里的结果表卡片：表名、行列数、前几行预览、查看全部 */
import { Maximize2, Table2 } from '@lucide/vue'
import { ref } from 'vue'

import DataTable from '@/components/common/DataTable.vue'
import type { TableArtifact } from '@/composables/chatModel'

const props = defineProps<{ table: TableArtifact }>()
const emit = defineEmits<{ open: [name: string] }>()
const expanded = ref(false)

const ROLE: Record<string, string> = { derived: '分析结果', result: '流程产出', input: '原始数据', expected: '期望结果', query: '查询结果' }
</script>

<template>
  <div class="card-soft animate-rise overflow-hidden">
    <div class="flex items-center gap-2.5 px-4 py-2.5">
      <Table2 class="size-4 text-fun-green" />
      <span class="truncate text-[13px] font-semibold">{{ table.label || table.name }}</span>
      <span class="tag">{{ ROLE[table.role ?? 'derived'] ?? '表' }}</span>
      <span class="font-mono text-[11px] text-faint">{{ table.rows }} 行 × {{ table.cols }} 列</span>
      <button v-if="table.name" class="btn-text ml-auto text-xs" @click="emit('open', table.name!)"><Maximize2 class="size-3.5" />查看全部</button>
      <button v-else-if="table.preview.length > 6" class="btn-text ml-auto text-xs" @click="expanded = !expanded">{{ expanded ? '收起' : '展开' }}</button>
    </div>
    <div class="px-3 pb-3">
      <DataTable :columns="props.table.columns" :rows="expanded ? table.preview : table.preview.slice(0, 6)" :max-height="expanded ? '360px' : '240px'" dense />
    </div>
  </div>
</template>

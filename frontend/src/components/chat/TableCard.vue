<script setup lang="ts">
/** 完整内联展示的小型临时表；正式文件使用文件卡片和 Univer。 */
import { Table2 } from '@lucide/vue'

import DataTable from '@/components/common/DataTable.vue'
import type { TableArtifact } from '@/composables/chatModel'

const props = defineProps<{ table: TableArtifact }>()
</script>

<template>
  <div class="card-soft animate-rise overflow-hidden" data-testid="inline-table">
    <div class="flex flex-wrap items-center gap-2.5 px-4 py-2.5">
      <Table2 class="size-4 shrink-0 text-fun-green" />
      <span class="min-w-0 truncate text-[13px] font-semibold">{{ table.label || table.name }}</span>
      <span class="font-mono text-[11px] text-faint">{{ table.rows }} 行 × {{ table.cols }} 列</span>
    </div>
    <div class="px-3 pb-3">
      <DataTable :columns="props.table.columns" :rows="table.preview.slice(0, table.rows)" max-height="none" dense />
    </div>
  </div>
</template>

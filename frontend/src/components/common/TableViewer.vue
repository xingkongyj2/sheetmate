<script setup lang="ts">
/** 查看整张表（分页）。loader 由调用方提供：会话工作区的表 / 运行中间表 / 上传文件的 sheet。 */
import { ChevronLeft, ChevronRight, LoaderCircle } from '@lucide/vue'
import { onMounted, ref } from 'vue'

import { errorText } from '@/api/http'

import BaseModal from './BaseModal.vue'
import DataTable from './DataTable.vue'

export interface PageResult {
  columns: string[]
  rows: unknown[][]
  total: number
}

const props = defineProps<{ title: string; subtitle?: string; loader: (offset: number, limit: number) => Promise<PageResult> }>()
const emit = defineEmits<{ close: [] }>()

const PAGE = 100
const offset = ref(0)
const page = ref<PageResult | null>(null)
const loading = ref(false)
const error = ref('')

async function load(next: number) {
  loading.value = true
  error.value = ''
  try {
    page.value = await props.loader(next, PAGE)
    offset.value = next
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}

onMounted(() => load(0))
</script>

<template>
  <BaseModal width="max-w-6xl" @close="emit('close')">
    <template #title>
      <div class="flex items-baseline gap-3">
        <h3 class="truncate text-base font-semibold">{{ title }}</h3>
        <span v-if="page" class="text-xs text-mute">{{ page.total }} 行 × {{ page.columns.length }} 列</span>
      </div>
      <p v-if="subtitle" class="mt-0.5 truncate text-xs text-faint">{{ subtitle }}</p>
    </template>
    <div v-if="error" class="rounded-xl bg-error/5 px-4 py-3 text-sm text-error">{{ error }}</div>
    <div v-else-if="!page" class="flex h-40 items-center justify-center text-mute"><LoaderCircle class="size-5 animate-spin" /></div>
    <DataTable v-else :columns="page.columns" :rows="page.rows" :offset="offset" max-height="62vh" />
    <template v-if="page && page.total > PAGE" #footer>
      <div class="flex w-full items-center justify-between text-sm text-mute">
        <span>第 {{ offset + 1 }}–{{ Math.min(offset + PAGE, page.total) }} 行，共 {{ page.total }} 行</span>
        <div class="flex gap-2">
          <button class="btn-line" :disabled="offset === 0 || loading" @click="load(Math.max(0, offset - PAGE))">
            <ChevronLeft class="size-4" />上一页
          </button>
          <button class="btn-line" :disabled="offset + PAGE >= page.total || loading" @click="load(offset + PAGE)">
            下一页<ChevronRight class="size-4" />
          </button>
        </div>
      </div>
    </template>
  </BaseModal>
</template>

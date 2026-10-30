<script setup lang="ts">
/** 客户记忆：业务规则 / 偏好 / 背景 / 经验，以及字段映射（标准字段 ← 各种表里的别名） */
import { Plus, Save, Trash2, X } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import { customerApi, memoryApi } from '@/api'
import { errorText } from '@/api/http'
import type { MemoryItem } from '@/api/types'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

const props = defineProps<{ customerId: string; memories: MemoryItem[]; mapping: Record<string, string[]> }>()
const emit = defineEmits<{ changed: [] }>()
const toast = useToast()
const { confirmDelete } = useConfirm()

const CATEGORIES = [
  { value: 'domain_knowledge', label: '业务规则' },
  { value: 'preferences', label: '偏好' },
  { value: 'user_context', label: '背景' },
  { value: 'history_insights', label: '经验' },
]
const category = ref('domain_knowledge')
const key = ref('')
const value = ref('')

const grouped = computed(() =>
  CATEGORIES.map((c) => ({ ...c, items: props.memories.filter((m) => m.category === c.value) })).filter((g) => g.items.length),
)

async function add() {
  if (!key.value.trim() || !value.value.trim()) return
  try {
    await customerApi.addMemory(props.customerId, { category: category.value, key: key.value.trim(), value: value.value.trim() })
    key.value = ''
    value.value = ''
    emit('changed')
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function remove(memory: MemoryItem) {
  if (!await confirmDelete({
    title: '删除这条记忆？',
    target: `${memory.key}：${memory.value}`,
    description: '这条记忆将从客户资料中移除，之后的对话不再使用它。',
    confirmText: '删除记忆',
    action: () => memoryApi.remove(memory.id),
  })) return
  emit('changed')
}

// ---- 字段映射
const rows = ref<{ standard: string; aliases: string }[]>([])
watch(
  () => props.mapping,
  (m) => (rows.value = Object.entries(m).map(([standard, aliases]) => ({ standard, aliases: aliases.join('，') }))),
  { immediate: true },
)

async function saveMapping() {
  const mapping: Record<string, string[]> = {}
  for (const r of rows.value) {
    const aliases = r.aliases.split(/[,，、\s]+/).map((s) => s.trim()).filter(Boolean)
    if (r.standard.trim() && aliases.length) mapping[r.standard.trim()] = aliases
  }
  try {
    await customerApi.saveMapping(props.customerId, mapping)
    toast.success('字段映射已保存，下次读入表格时会自动统一列名')
    emit('changed')
  } catch (err) {
    toast.error(errorText(err))
  }
}
</script>

<template>
  <div class="grid gap-6 lg:grid-cols-2">
    <section class="card-soft p-5">
      <h3 class="text-[15px] font-semibold">客户记忆</h3>
      <p class="mt-1 text-xs text-mute">对话时 AI 会遵守这些规则；对话中提到的新规则也会自动记到这里。</p>
      <div class="mt-4 space-y-4">
        <div v-for="g in grouped" :key="g.value">
          <div class="label-mini mb-1.5">{{ g.label }}</div>
          <div v-for="m in g.items" :key="m.id" class="group flex items-start gap-2 rounded-xl px-2 py-1.5 hover:bg-paper">
            <div class="min-w-0 flex-1 text-[13px]">
              <span class="font-medium">{{ m.key }}</span>
              <span class="text-mute">：{{ m.value }}</span>
              <span class="ml-1 text-[10px] text-faint">{{ m.source === 'ai' ? 'AI 记录' : '人工' }}</span>
            </div>
            <button class="rounded p-1 text-faint opacity-0 hover:text-error focus:opacity-100 group-hover:opacity-100" :aria-label="`删除记忆 ${m.key}`" @click="remove(m)"><Trash2 class="size-3.5" /></button>
          </div>
        </div>
        <p v-if="!memories.length" class="py-4 text-center text-sm text-faint">还没有记忆</p>
      </div>
      <div class="mt-4 space-y-2 border-t border-line pt-4">
        <div class="flex gap-2">
          <select v-model="category" class="select select-sm w-28 rounded-xl">
            <option v-for="c in CATEGORIES" :key="c.value" :value="c.value">{{ c.label }}</option>
          </select>
          <input v-model="key" class="field py-1.5" placeholder="名称，例如：归属兜底" />
        </div>
        <div class="flex gap-2">
          <input v-model="value" class="field py-1.5" placeholder="内容，例如：匹配不到归属时记为新食研" @keydown.enter="add" />
          <button class="btn-ink px-3 py-1.5" @click="add"><Plus class="size-4" />添加</button>
        </div>
      </div>
    </section>

    <section class="card-soft p-5">
      <h3 class="text-[15px] font-semibold">字段映射</h3>
      <p class="mt-1 text-xs text-mute">不同表里同一个字段名字不一样（单号 / 运单号）时，读入时自动统一成标准名。语义不同的字段（如 ERP单号 和 订单号）不要配成别名。</p>
      <div class="mt-4 space-y-2">
        <div class="grid grid-cols-[1fr_1.6fr_auto] gap-2 px-1 text-[11px] text-faint"><span>标准字段名</span><span>别名（逗号分隔）</span><span /></div>
        <div v-for="(r, i) in rows" :key="i" class="grid grid-cols-[1fr_1.6fr_auto] items-center gap-2">
          <input v-model="r.standard" class="field py-1.5" placeholder="运单号" />
          <input v-model="r.aliases" class="field py-1.5" placeholder="单号，运单编号" />
          <button class="icon-btn hover:text-error" @click="rows.splice(i, 1)"><X class="size-4" /></button>
        </div>
      </div>
      <div class="mt-3 flex gap-2">
        <button class="btn-text" @click="rows.push({ standard: '', aliases: '' })"><Plus class="size-3.5" />加一行</button>
        <button class="btn-ink ml-auto py-2" @click="saveMapping"><Save class="size-4" />保存映射</button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { Check, LoaderCircle, Pencil, Plus, Save, Trash2, X } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { customerApi, memoryApi } from '@/api'
import { errorText } from '@/api/http'
import type { FieldMappingEntry, MemoryItem } from '@/api/types'
import { timeAgo } from '@/composables/chatModel'
import { formatAliases, normalizeAliasSeparators, parseAliases, tidyAliases } from '@/composables/fieldAliases'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

const props = defineProps<{
  customerId: string
  recipeId?: string
  memories: MemoryItem[]
  mapping: Record<string, string[]>
  mappingEntries: FieldMappingEntry[]
  refresh: () => Promise<void>
}>()
const toast = useToast()
const { confirmDelete } = useConfirm()
const value = ref('')
const editing = ref<number | null>(null)
const editValue = ref('')
const saving = ref(false)
const savingMapping = ref(false)
const humanMemories = computed(() => props.memories.filter((m) => m.source === 'human'))
const repairExperiences = computed(() => props.memories.filter((m) =>
  m.source === 'ai' && m.category === 'history_insights'
  && m.execution_evidence?.kind === 'verified_repair' && m.execution_evidence.verified === true,
))
let active = true
onBeforeUnmount(() => { active = false })

async function add() {
  const text = value.value.trim()
  if (!text || saving.value) return
  const customerId = props.customerId
  const recipeId = props.recipeId
  saving.value = true
  try {
    await customerApi.addMemory(customerId, { value: text }, recipeId)
    if (active) {
      value.value = ''
    }
    await props.refresh()
  } catch (err) {
    if (active) toast.error(errorText(err))
  } finally {
    saving.value = false
  }
}

function edit(memory: MemoryItem) {
  editing.value = memory.id
  editValue.value = memory.value
}

async function saveMemory(memory: MemoryItem) {
  const text = editValue.value.trim()
  if (!text || saving.value) return
  saving.value = true
  try {
    await memoryApi.update(memory.id, { value: text })
    if (active) {
      editing.value = null
    }
    await props.refresh()
  } catch (err) {
    if (active) toast.error(errorText(err))
  } finally {
    saving.value = false
  }
}

async function remove(memory: MemoryItem) {
  if (!await confirmDelete({
    title: '删除这条记忆？', target: memory.value,
    description: '后续处理将不再使用这条记忆。', confirmText: '删除记忆',
    action: () => memoryApi.remove(memory.id),
  })) return
  await props.refresh()
}

type MappingRow = { standard: string; aliases: string; source: 'ai' | 'human' }

/** 别名输入：敲英文逗号 / 顿号就立刻换成中文逗号。替换是一对一的，光标位置原样恢复；输入法正在组字时不动，免得打断输入。 */
function typeAliases(row: MappingRow, event: Event) {
  const input = event.target as HTMLInputElement
  row.source = 'human'
  if ((event as InputEvent).isComposing) { row.aliases = input.value; return }
  const fixed = normalizeAliasSeparators(input.value)
  if (fixed !== input.value) {
    const [start, end] = [input.selectionStart ?? fixed.length, input.selectionEnd ?? fixed.length]
    input.value = fixed
    input.setSelectionRange(start, end)
  }
  row.aliases = fixed
}
function finishAliases(row: MappingRow, event: Event) {
  const input = event.target as HTMLInputElement
  row.aliases = input.value = tidyAliases(input.value)  // 离开输入框时再整理：去掉逗号旁的空格和多余的逗号
}
const rows = ref<MappingRow[]>([])
watch(() => [props.mapping, props.mappingEntries] as const, () => {
  const sources = new Map(props.mappingEntries.map((entry) => [entry.standard, entry.source]))
  rows.value = Object.entries(props.mapping).map(([standard, aliases]) => ({
    standard, aliases: formatAliases(aliases), source: sources.get(standard) ?? 'human',
  }))
}, { immediate: true })

async function saveMapping() {
  if (savingMapping.value) return
  const mapping: Record<string, string[]> = {}
  for (const row of rows.value) {
    const standard = row.standard.trim()
    const aliases = parseAliases(row.aliases)
    if (!standard && !aliases.length) continue
    if (!standard || !aliases.length) return toast.error('请填写标准字段和对应别名')
    if (Object.hasOwn(mapping, standard)) return toast.error(`标准字段「${standard}」重复了`)
    mapping[standard] = aliases
  }
  const customerId = props.customerId
  const recipeId = props.recipeId
  savingMapping.value = true
  try {
    await customerApi.saveMapping(customerId, mapping, recipeId)
    if (active) {
      toast.success('字段映射已保存')
    }
    await props.refresh()
  } catch (err) {
    if (active) toast.error(errorText(err))
  } finally {
    savingMapping.value = false
  }
}
</script>

<template>
  <section class="card-soft p-5 sm:p-6">
    <!-- 记忆 -->
    <h3 class="mb-3 text-[15px] font-semibold">记忆</h3>
    <article v-for="memory in humanMemories" :key="memory.id" class="-mx-2 rounded-xl px-2 py-2 transition hover:bg-paper">
      <template v-if="editing === memory.id">
        <textarea v-model="editValue" class="field min-h-20 resize-y text-[13px] leading-6" aria-label="编辑记忆" @keydown.ctrl.enter.prevent="saveMemory(memory)" @keydown.meta.enter.prevent="saveMemory(memory)" />
        <div class="mt-2 flex justify-end gap-2">
          <button class="btn-text text-xs" :disabled="saving" @click="editing = null">取消</button>
          <button class="btn-ink px-3 py-1.5 text-xs" :disabled="saving || !editValue.trim()" @click="saveMemory(memory)"><LoaderCircle v-if="saving" class="size-3.5 animate-spin" /><Check v-else class="size-3.5" />保存</button>
        </div>
      </template>
      <div v-else class="flex items-start gap-2">
        <div class="min-w-0 flex-1">
          <p class="whitespace-pre-wrap break-words text-sm leading-6">{{ memory.value }}</p>
          <p class="text-[11px] text-faint">{{ timeAgo(memory.updated_at) }}</p>
        </div>
        <button class="icon-btn shrink-0" title="编辑" aria-label="编辑记忆" :disabled="saving" @click="edit(memory)"><Pencil class="size-4" /></button>
        <button class="icon-btn shrink-0 hover:text-error" title="删除" aria-label="删除记忆" :disabled="saving" @click="remove(memory)"><Trash2 class="size-4" /></button>
      </div>
    </article>
    <div class="flex items-end gap-2" :class="humanMemories.length ? 'mt-2' : ''">
      <textarea v-model="value" rows="1" class="field min-h-10 flex-1 resize-none py-2 text-[13px] leading-6" placeholder="添加一条记忆" aria-label="新的记忆" @keydown.ctrl.enter.prevent="add" @keydown.meta.enter.prevent="add" />
      <button class="btn-ink h-10 shrink-0 gap-1.5 px-4 py-0 text-xs" :disabled="saving || !value.trim()" @click="add"><LoaderCircle v-if="saving" class="size-3.5 animate-spin" /><Plus v-else class="size-3.5" />添加</button>
    </div>

    <!-- 修复经验：有才显示 -->
    <template v-if="repairExperiences.length">
      <h3 class="mb-3 mt-6 border-t border-line pt-6 text-[15px] font-semibold">修复经验</h3>
      <article v-for="memory in repairExperiences" :key="memory.id" class="-mx-2 flex items-start gap-2 rounded-xl px-2 py-2 transition hover:bg-paper">
        <div class="min-w-0 flex-1">
          <p class="whitespace-pre-wrap break-words text-sm leading-6">{{ memory.value }}</p>
          <p class="text-[11px] text-faint">{{ timeAgo(memory.updated_at) }}</p>
        </div>
        <button class="icon-btn shrink-0 hover:text-error" title="删除" aria-label="删除修复经验" :disabled="saving" @click="remove(memory)"><Trash2 class="size-4" /></button>
      </article>
    </template>

    <!-- 字段映射 -->
    <h3 class="mb-3 mt-6 border-t border-line pt-6 text-[15px] font-semibold">字段映射</h3>
    <div v-for="(row, index) in rows" :key="index" class="mb-2 flex items-center gap-2">
      <input v-model="row.standard" class="field min-w-0 flex-1 py-2 text-[13px]" placeholder="标准字段，如 运单号" :aria-label="`第 ${index + 1} 行标准字段`" @input="row.source = 'human'" />
      <input :value="row.aliases" class="field min-w-0 flex-[1.6] py-2 text-[13px]" placeholder="别名，用逗号分隔" :aria-label="`第 ${index + 1} 行字段别名`" @input="typeAliases(row, $event)" @blur="finishAliases(row, $event)" />
      <span class="w-7 shrink-0 text-center"><span v-if="row.source === 'ai'" class="rounded-md bg-[#6265DC]/8 px-1.5 py-0.5 text-[10px] text-[#6265DC]" title="AI 关联多表时自动发现">AI</span></span>
      <button class="icon-btn shrink-0 hover:text-error" :aria-label="`移除字段映射 ${row.standard || index + 1}`" title="移除" @click="rows.splice(index, 1)"><X class="size-4" /></button>
    </div>
    <div class="flex items-center justify-between gap-2" :class="rows.length ? 'mt-3' : ''">
      <button class="btn-text px-2 text-xs" @click="rows.push({ standard: '', aliases: '', source: 'human' })"><Plus class="size-3.5" />添加字段</button>
      <button v-if="rows.length || Object.keys(mapping).length" class="btn-ink h-9 gap-1.5 px-4 py-0 text-xs" :disabled="savingMapping" @click="saveMapping"><LoaderCircle v-if="savingMapping" class="size-3.5 animate-spin" /><Save v-else class="size-3.5" />保存</button>
    </div>
  </section>
</template>

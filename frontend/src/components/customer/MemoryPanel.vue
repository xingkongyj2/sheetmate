<script setup lang="ts">
import { Check, LoaderCircle, Pencil, Plus, Save, Trash2, X } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { customerApi, memoryApi } from '@/api'
import { errorText } from '@/api/http'
import type { FieldMappingEntry, MemoryItem } from '@/api/types'
import { timeAgo } from '@/composables/chatModel'
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
const rows = ref<MappingRow[]>([])
watch(() => [props.mapping, props.mappingEntries] as const, () => {
  const sources = new Map(props.mappingEntries.map((entry) => [entry.standard, entry.source]))
  rows.value = Object.entries(props.mapping).map(([standard, aliases]) => ({
    standard, aliases: aliases.join('，'), source: sources.get(standard) ?? 'human',
  }))
}, { immediate: true })

async function saveMapping() {
  if (savingMapping.value) return
  const mapping: Record<string, string[]> = {}
  for (const row of rows.value) {
    const standard = row.standard.trim()
    const aliases = [...new Set(row.aliases.split(/[,，、\n]+/).map((s) => s.trim()).filter(Boolean))]
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
  <div class="space-y-6">
    <section class="card-soft p-5 sm:p-6">
      <div class="flex flex-wrap items-center gap-2">
        <h3 class="text-[15px] font-semibold">客户记忆</h3>
        <span class="rounded-full bg-[#6265DC]/8 px-2 py-0.5 text-[10px] text-[#6265DC]">优先遵循</span>
      </div>
      <p class="mt-1.5 text-xs leading-5 text-mute">记录这个流程需要记住的规则，后续处理优先使用。</p>
      <div class="mt-4 space-y-2">
        <article v-for="memory in humanMemories" :key="memory.id" class="rounded-xl bg-paper/60 px-3 py-3">
          <template v-if="editing === memory.id">
            <textarea v-model="editValue" class="field min-h-24 resize-y text-[13px] leading-6" aria-label="编辑记忆" @keydown.ctrl.enter.prevent="saveMemory(memory)" @keydown.meta.enter.prevent="saveMemory(memory)" />
            <div class="mt-2 flex justify-end gap-2">
              <button class="btn-text text-xs" :disabled="saving" @click="editing = null">取消</button>
              <button class="btn-ink px-3 py-1.5 text-xs" :disabled="saving || !editValue.trim()" @click="saveMemory(memory)"><LoaderCircle v-if="saving" class="size-3.5 animate-spin" /><Check v-else class="size-3.5" />保存</button>
            </div>
          </template>
          <template v-else>
            <div class="flex items-start gap-2">
              <p class="min-w-0 flex-1 whitespace-pre-wrap break-words text-[13px] leading-6">{{ memory.value }}</p>
              <button class="icon-btn size-7 shrink-0" title="编辑记忆" aria-label="编辑记忆" :disabled="saving" @click="edit(memory)"><Pencil class="size-3.5" /></button>
              <button class="icon-btn size-7 shrink-0 hover:text-error" title="删除记忆" aria-label="删除记忆" :disabled="saving" @click="remove(memory)"><Trash2 class="size-3.5" /></button>
            </div>
            <p class="mt-1 text-[10px] text-faint">{{ timeAgo(memory.updated_at) }}</p>
          </template>
        </article>
        <p v-if="!humanMemories.length" class="py-5 text-center text-sm text-faint">还没有记忆</p>
      </div>
      <div class="mt-4 border-t border-line pt-4">
        <textarea v-model="value" class="field min-h-24 resize-y text-[13px] leading-6" placeholder="记下需要长期遵循的规则…" aria-label="新的客户记忆" @keydown.ctrl.enter.prevent="add" @keydown.meta.enter.prevent="add" />
        <div class="mt-2 flex justify-end">
          <button class="btn-ink px-3 py-2 text-xs" :disabled="saving || !value.trim()" @click="add"><LoaderCircle v-if="saving" class="size-3.5 animate-spin" /><Plus v-else class="size-3.5" />添加记忆</button>
        </div>
      </div>
    </section>

    <section v-if="repairExperiences.length" class="card-soft p-5 sm:p-6">
      <div class="flex flex-wrap items-center gap-2">
        <h3 class="text-[15px] font-semibold">修复经验</h3>
        <span class="rounded-full bg-[#6265DC]/8 px-2 py-0.5 text-[10px] text-[#6265DC]">已执行核对</span>
      </div>
      <p class="mt-1.5 text-xs leading-5 text-mute">从修复成功的操作中积累，供当前客户和流程遇到类似错误时参考，业务规则仍以你的要求为准。</p>
      <div class="mt-4 space-y-2">
        <article v-for="memory in repairExperiences" :key="memory.id" class="rounded-xl bg-paper/60 px-3 py-3">
          <div class="flex items-start gap-2">
            <div class="min-w-0 flex-1">
              <p class="whitespace-pre-wrap break-words text-[13px] leading-6">{{ memory.value }}</p>
            </div>
            <button class="icon-btn size-7 shrink-0 hover:text-error" title="删除修复经验" aria-label="删除修复经验" :disabled="saving" @click="remove(memory)"><Trash2 class="size-3.5" /></button>
          </div>
          <p class="mt-1 text-[10px] text-faint">{{ timeAgo(memory.updated_at) }}</p>
        </article>
      </div>
    </section>

    <section class="card-soft p-5 sm:p-6">
      <h3 class="text-[15px] font-semibold">字段映射</h3>
      <p class="mt-1.5 text-xs leading-5 text-mute">AI 关联多表时自动发现，也可以手动补充同义字段。</p>
      <div class="mt-4 space-y-2">
        <div class="grid grid-cols-[1fr_1.5fr_28px] gap-2 text-[11px] text-faint sm:grid-cols-[1fr_1.5fr_68px_28px]"><span>标准字段</span><span>别名，逗号分隔</span><span class="hidden sm:block">来源</span><span /></div>
        <div v-for="(row, index) in rows" :key="index" class="grid grid-cols-[1fr_1.5fr_28px] items-center gap-2 sm:grid-cols-[1fr_1.5fr_68px_28px]">
          <input v-model="row.standard" class="field min-w-0 py-2 text-xs" placeholder="运单号" :aria-label="`第 ${index + 1} 行标准字段`" @input="row.source = 'human'" />
          <input v-model="row.aliases" class="field min-w-0 py-2 text-xs" placeholder="单号，运单编号" :aria-label="`第 ${index + 1} 行字段别名`" @input="row.source = 'human'" />
          <span class="hidden text-[10px] sm:block" :class="row.source === 'ai' ? 'text-[#6265DC]' : 'text-mute'">{{ row.source === 'ai' ? 'AI 发现' : '手动添加' }}</span>
          <button class="icon-btn size-7 hover:text-error" :aria-label="`移除字段映射 ${row.standard || index + 1}`" @click="rows.splice(index, 1)"><X class="size-3.5" /></button>
          <span class="col-span-2 -mt-1 text-[10px] sm:hidden" :class="row.source === 'ai' ? 'text-[#6265DC]' : 'text-faint'">{{ row.source === 'ai' ? 'AI 发现' : '手动添加' }}</span>
        </div>
        <p v-if="!rows.length" class="py-5 text-center text-sm text-faint">还没有字段映射</p>
      </div>
      <div class="mt-4 flex items-center justify-between gap-2">
        <button class="btn-text text-xs" @click="rows.push({ standard: '', aliases: '', source: 'human' })"><Plus class="size-3.5" />手动添加</button>
        <button class="btn-ink px-3 py-2 text-xs" :disabled="savingMapping" @click="saveMapping"><LoaderCircle v-if="savingMapping" class="size-3.5 animate-spin" /><Save v-else class="size-3.5" />保存映射</button>
      </div>
    </section>
  </div>
</template>

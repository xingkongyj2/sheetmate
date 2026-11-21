<script setup lang="ts">
import { ArrowRight, Check, LoaderCircle, Pencil, Plus, Save, Trash2, X } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { customerApi, memoryApi } from '@/api'
import { errorText } from '@/api/http'
import type { FieldMappingEntry, MemoryItem } from '@/api/types'
import CustomerEmptyIllustration from '@/components/brand/CustomerEmptyIllustration.vue'
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
const standardInputs = ref<HTMLInputElement[]>([])
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

async function addMappingRow() {
  rows.value.push({ standard: '', aliases: '', source: 'human' })
  await nextTick()
  standardInputs.value[rows.value.length - 1]?.focus()
}

const canSaveMapping = computed(() => rows.value.length > 0 || Object.keys(props.mapping).length > 0)

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
  <div class="space-y-4">
    <!-- 记忆卡片 -->
    <section class="rounded-[24px] bg-white p-5 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.10),0_0_0_1px_rgba(34,34,66,0.035)] sm:p-6">
      <header class="mb-4 flex items-center gap-2">
        <h3 class="text-[15px] font-semibold text-ink">记忆</h3>
        <span v-if="humanMemories.length" class="rounded-full bg-paper px-2 py-0.5 text-[11px] font-medium tabular-nums text-mute">{{ humanMemories.length }}</span>
      </header>

      <div v-if="humanMemories.length" class="mb-4 space-y-2">
        <article
          v-for="memory in humanMemories"
          :key="memory.id"
          class="group rounded-2xl bg-[#FAF9F6] px-4 py-3 transition hover:bg-paper/80"
        >
          <template v-if="editing === memory.id">
            <textarea
              v-model="editValue"
              class="w-full min-h-20 resize-y rounded-xl border border-line bg-white px-3 py-2 text-[13px] leading-6 text-ink outline-none transition focus:border-ink/30"
              aria-label="编辑记忆"
              @keydown.ctrl.enter.prevent="saveMemory(memory)"
              @keydown.meta.enter.prevent="saveMemory(memory)"
            />
            <div class="mt-2 flex justify-end gap-1.5">
              <button type="button" class="btn-text h-7 px-2.5 text-xs" :disabled="saving" @click="editing = null">取消</button>
              <button type="button" class="btn-ink h-7 gap-1 px-3 py-0 text-xs" :disabled="saving || !editValue.trim()" @click="saveMemory(memory)">
                <LoaderCircle v-if="saving" class="size-3 animate-spin" /><Check v-else class="size-3.5" />保存
              </button>
            </div>
          </template>
          <div v-else class="flex items-start gap-3">
            <span class="mt-2 size-1.5 shrink-0 rounded-full bg-[#6265DC]/65" aria-hidden="true" />
            <div class="min-w-0 flex-1">
              <p class="whitespace-pre-wrap break-words text-[13.5px] leading-6 text-ink">{{ memory.value }}</p>
              <p class="mt-1 text-[11px] text-faint">{{ timeAgo(memory.updated_at) }}</p>
            </div>
            <div class="-mr-1 flex shrink-0 items-center gap-0.5 opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
              <button type="button" class="icon-btn size-7" title="编辑" aria-label="编辑记忆" :disabled="saving" @click="edit(memory)"><Pencil class="size-3.5" /></button>
              <button type="button" class="icon-btn size-7 hover:text-error" title="删除" aria-label="删除记忆" :disabled="saving" @click="remove(memory)"><Trash2 class="size-3.5" /></button>
            </div>
          </div>
        </article>
      </div>
      <div v-else class="flex flex-col items-center pb-5 pt-2 text-center">
        <CustomerEmptyIllustration kind="memory" size="sm" />
        <p class="mt-2 text-sm text-faint">还没有记忆</p>
      </div>

      <!-- 一体化输入条 -->
      <div class="flex items-end gap-2 rounded-2xl border border-line/90 bg-[#FAF9F7] p-1.5 pl-3.5 transition focus-within:border-ink/25 focus-within:bg-white focus-within:shadow-[0_2px_12px_-4px_rgba(34,34,66,0.08)]">
        <textarea
          v-model="value"
          rows="1"
          class="min-h-8 flex-1 resize-none bg-transparent py-1.5 text-[13.5px] leading-5 text-ink outline-none placeholder:text-faint"
          placeholder="添加一条记忆…"
          aria-label="新的记忆"
          @keydown.ctrl.enter.prevent="add"
          @keydown.meta.enter.prevent="add"
        />
        <button
          type="button"
          class="inline-flex h-8 shrink-0 items-center justify-center gap-1 rounded-xl px-3 text-xs font-medium transition"
          :class="value.trim() && !saving ? 'bg-ink text-white hover:bg-ink/90 cursor-pointer' : 'bg-black/[0.05] text-faint cursor-not-allowed'"
          :disabled="saving || !value.trim()"
          @click="add"
        >
          <LoaderCircle v-if="saving" class="size-3.5 animate-spin" /><Plus v-else class="size-3.5" />添加
        </button>
      </div>

      <!-- 修复经验：有才显示 -->
      <template v-if="repairExperiences.length">
        <header class="mb-3 mt-6 flex items-center gap-2 border-t border-line/80 pt-5">
          <h3 class="text-[15px] font-semibold text-ink">修复经验</h3>
          <span class="rounded-full bg-paper px-2 py-0.5 text-[11px] font-medium tabular-nums text-mute">{{ repairExperiences.length }}</span>
        </header>
        <div class="space-y-2">
          <article
            v-for="memory in repairExperiences"
            :key="memory.id"
            class="group flex items-start gap-3 rounded-2xl bg-[#FAF9F6] px-4 py-3 transition hover:bg-paper/80"
          >
            <span class="mt-2 size-1.5 shrink-0 rounded-full bg-[#22B573]/75" aria-hidden="true" />
            <div class="min-w-0 flex-1">
              <p class="whitespace-pre-wrap break-words text-[13.5px] leading-6 text-ink">{{ memory.value }}</p>
              <p class="mt-1 text-[11px] text-faint">{{ timeAgo(memory.updated_at) }}</p>
            </div>
            <button
              type="button"
              class="icon-btn -mr-1 size-7 shrink-0 opacity-100 transition hover:text-error sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100"
              title="删除"
              aria-label="删除修复经验"
              :disabled="saving"
              @click="remove(memory)"
            ><Trash2 class="size-3.5" /></button>
          </article>
        </div>
      </template>
    </section>

    <!-- 字段映射卡片 -->
    <section class="rounded-[24px] bg-white p-5 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.10),0_0_0_1px_rgba(34,34,66,0.035)] sm:p-6">
      <header class="mb-4 flex items-center gap-2">
        <h3 class="text-[15px] font-semibold text-ink">字段映射</h3>
        <span v-if="rows.length" class="rounded-full bg-paper px-2 py-0.5 text-[11px] font-medium tabular-nums text-mute">{{ rows.length }}</span>
        <div class="ml-auto flex shrink-0 items-center gap-2">
          <button
            type="button"
            class="h-8 gap-1.5 whitespace-nowrap px-3 py-0 text-xs"
            :class="canSaveMapping ? 'btn-line' : 'btn-ink'"
            @click="addMappingRow"
          ><Plus class="size-3.5" />添加字段</button>
          <button
            v-if="canSaveMapping"
            type="button"
            class="btn-ink h-8 gap-1.5 whitespace-nowrap px-3.5 py-0 text-xs"
            :disabled="savingMapping"
            @click="saveMapping"
          ><LoaderCircle v-if="savingMapping" class="size-3.5 animate-spin" /><Save v-else class="size-3.5" />保存</button>
        </div>
      </header>

      <div v-if="rows.length" class="overflow-hidden rounded-2xl border border-line/80 bg-white">
        <div class="grid grid-cols-[minmax(0,1fr)_20px_minmax(0,1.65fr)_56px] items-center gap-2 border-b border-line/70 bg-[#FAF9F6] px-3.5 py-2 text-[11px] font-medium text-mute">
          <span>标准字段</span>
          <span aria-hidden="true" />
          <span>对应别名</span>
          <span class="sr-only">操作</span>
        </div>
        <div class="divide-y divide-line/60">
          <div
            v-for="(row, index) in rows"
            :key="index"
            class="group grid grid-cols-[minmax(0,1fr)_20px_minmax(0,1.65fr)_56px] items-center gap-2 px-2 py-1.5 transition hover:bg-[#FAF9F6]/60"
          >
            <input
              :ref="(el) => { if (el) standardInputs[index] = el as HTMLInputElement }"
              v-model="row.standard"
              class="min-w-0 rounded-xl bg-transparent px-2.5 py-1.5 text-[13px] font-medium text-ink outline-none transition placeholder:font-normal placeholder:text-faint hover:bg-paper/60 focus:bg-paper/75"
              placeholder="如 运单号"
              :aria-label="`第 ${index + 1} 行标准字段`"
              @input="row.source = 'human'"
            />
            <ArrowRight class="mx-auto size-3.5 text-faint" aria-hidden="true" />
            <input
              :value="row.aliases"
              class="min-w-0 rounded-xl bg-transparent px-2.5 py-1.5 text-[13px] text-ink outline-none transition placeholder:text-faint hover:bg-paper/60 focus:bg-paper/75"
              placeholder="用逗号分隔，如 单号，快递单号"
              :aria-label="`第 ${index + 1} 行字段别名`"
              @input="typeAliases(row, $event)"
              @blur="finishAliases(row, $event)"
            />
            <div class="flex items-center justify-end gap-1 pr-0.5">
              <span v-if="row.source === 'ai'" class="rounded-md bg-[#6265DC]/10 px-1.5 py-0.5 text-[10px] font-medium text-[#6265DC]" title="AI 关联多表时自动发现">AI</span>
              <button
                type="button"
                class="icon-btn size-7 shrink-0 text-faint hover:text-error"
                :aria-label="`移除字段映射 ${row.standard || index + 1}`"
                title="移除"
                @click="rows.splice(index, 1)"
              ><X class="size-3.5" /></button>
            </div>
          </div>
        </div>
      </div>

      <div v-else class="flex flex-col items-center py-6 text-center">
        <CustomerEmptyIllustration kind="mapping" size="sm" />
        <p class="mt-2 text-sm text-faint">还没有字段映射</p>
      </div>
    </section>
  </div>
</template>

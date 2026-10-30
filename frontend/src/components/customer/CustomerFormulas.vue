<script setup lang="ts">
/**
 * 客户公式：内置公式覆盖不了的客户独有逻辑，写成「参数声明 + {{参数}} SQL 模板」，流程里像内置公式一样调用。
 * 列表显示每个公式的最新版本、参数、被哪些流程用到（以及这些流程现在有没有问题）；新建 / 编辑 / 试运行都在编辑器里。
 */
import { FlaskConical, FunctionSquare, Pencil, Plus, Power, Trash2, TriangleAlert } from '@lucide/vue'
import { ref } from 'vue'

import { customerApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerFormula, FileInfo } from '@/api/types'
import StatusPill from '@/components/common/StatusPill.vue'
import { timeAgo } from '@/composables/chatModel'
import { paramTypeLabel } from '@/composables/formulaDraft'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

import FormulaEditorDialog from './FormulaEditorDialog.vue'

const props = defineProps<{ customerId: string; formulas: CustomerFormula[]; files: FileInfo[]; fieldMapping: Record<string, string[]> }>()
const emit = defineEmits<{ changed: [] }>()
const toast = useToast()
const { confirmDelete } = useConfirm()

const editor = ref<{ formula: CustomerFormula | null; trial: boolean } | null>(null)

const problemsOf = (f: CustomerFormula) => f.used_by.flatMap((u) => u.problems.map((p) => `流程「${u.name}」${p}`))

async function toggle(f: CustomerFormula) {
  const next = f.status === 'deprecated' ? 'active' : 'deprecated'
  try {
    await customerApi.setFormulaStatus(props.customerId, f.id, next)
    toast.success(next === 'active' ? `已启用「${f.name}」` : `已停用「${f.name}」`)
    emit('changed')
  } catch (err) {
    toast.error(errorText(err))
  }
}

async function remove(f: CustomerFormula) {
  const customerId = props.customerId
  if (!await confirmDelete({
    title: '删除这个公式？',
    target: `${f.name}（${f.id}）`,
    description: '这个公式的所有版本都会删除，删除后无法恢复。',
    confirmText: '删除公式',
    action: () => customerApi.removeFormula(customerId, f.id),
  })) return
  toast.success('已删除')
  emit('changed')
}

function onSaved(saved: CustomerFormula) {
  editor.value = null
  const broken = problemsOf(saved).length
  toast.success(broken ? `已保存「${saved.name}」v${saved.version}；有流程需要调整，见列表里的红字` : `已保存「${saved.name}」v${saved.version}`)
  emit('changed')
}
</script>

<template>
  <section class="card-soft overflow-hidden" data-testid="customer-formulas">
    <div class="flex flex-wrap items-center gap-3 border-b border-line px-5 py-4">
      <FunctionSquare class="size-4" />
      <h2 class="text-[15px] font-semibold">客户公式</h2>
      <span class="tag">{{ formulas.length }}</span>
      <p class="min-w-0 flex-1 basis-72 text-xs leading-relaxed text-mute">
        内置公式覆盖不了的客户独有逻辑，写成「参数 + SQL 模板」，流程里就能像内置公式一样调用。id 和内置公式相同时，这个客户改用你写的版本。
      </p>
      <button class="btn-ink py-2" data-testid="new-formula" @click="editor = { formula: null, trial: false }"><Plus class="size-4" />新建公式</button>
    </div>

    <p v-if="!formulas.length" class="py-12 text-center text-sm text-faint">还没有客户公式</p>

    <ul v-else>
      <li v-for="f in formulas" :key="f.id" class="border-t border-line px-5 py-4 first:border-0" :class="f.status === 'deprecated' ? 'bg-paper/50' : ''" data-testid="formula-row">
        <div class="flex flex-wrap items-start gap-3">
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="text-[15px] font-semibold" :class="f.status === 'deprecated' ? 'text-mute' : ''">{{ f.name }}</h3>
              <span class="font-mono text-xs text-faint">{{ f.id }}</span>
              <span class="tag">v{{ f.version }}</span>
              <StatusPill :status="f.status" :label="f.status_label" />
              <span v-if="f.overrides_builtin" class="tag !bg-warning/15 !text-[#8a5b00]" title="流程里用到这个 id 的步骤，在这个客户这里会改用你写的版本">覆盖内置「{{ f.overrides_name }}」</span>
            </div>
            <p v-if="f.intent" class="mt-1 text-[13px] text-mute">{{ f.intent }}</p>
            <div class="mt-2 flex flex-wrap items-center gap-1.5">
              <span v-for="(p, name) in f.params" :key="name" class="chip !py-0.5 font-mono" :title="p.desc || undefined">
                {{ name }}<span v-if="p.required" class="text-error">*</span><span class="font-sans text-faint">{{ paramTypeLabel(p.type) }}</span>
              </span>
              <span v-if="!Object.keys(f.params).length" class="text-xs text-faint">没有参数</span>
            </div>
            <p class="mt-2 text-xs text-faint">
              <template v-if="f.used_by.length">被流程 {{ f.used_by.map((u) => `「${u.name}」`).join('') }} 使用（第 {{ [...new Set(f.used_by.flatMap((u) => u.steps.map((s) => s.id)))].join('、') }} 步）</template>
              <template v-else>还没有流程用到它</template>
              · 更新于 {{ timeAgo(f.updated_at) }}
            </p>
            <ul v-if="problemsOf(f).length" class="mt-2 space-y-0.5 rounded-xl bg-error/5 px-3 py-2 text-xs text-error">
              <li v-for="p in problemsOf(f)" :key="p" class="flex gap-1.5"><TriangleAlert class="mt-0.5 size-3.5 shrink-0" />{{ p }}</li>
            </ul>
          </div>
          <div class="flex shrink-0 items-center gap-0.5">
            <button class="btn-text" data-testid="trial-formula" @click="editor = { formula: f, trial: true }"><FlaskConical class="size-3.5" />试运行</button>
            <button class="icon-btn" title="编辑（保存为新版本）" data-testid="edit-formula" @click="editor = { formula: f, trial: false }"><Pencil class="size-4" /></button>
            <button
              class="icon-btn disabled:cursor-not-allowed disabled:opacity-40"
              :title="f.status !== 'deprecated' && f.used_by.length ? '被流程用到，不能停用' : f.status === 'deprecated' ? '启用' : '停用'"
              :disabled="f.status !== 'deprecated' && f.used_by.length > 0"
              @click="toggle(f)"
            >
              <Power class="size-4" />
            </button>
            <button
              class="icon-btn hover:text-error disabled:cursor-not-allowed disabled:opacity-40"
              :title="f.used_by.length ? '被流程用到，不能删除' : '删除'"
              :disabled="f.used_by.length > 0"
              @click="remove(f)"
            >
              <Trash2 class="size-4" />
            </button>
          </div>
        </div>
      </li>
    </ul>

    <FormulaEditorDialog
      v-if="editor"
      :customer-id="customerId"
      :formula="editor.formula"
      :existing-ids="formulas.map((f) => f.id)"
      :files="files"
      :field-mapping="fieldMapping"
      :focus-trial="editor.trial"
      @close="editor = null"
      @saved="onSaved"
    />
  </section>
</template>

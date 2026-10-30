<script setup lang="ts">
/** 公式库：内置公式（全局只读）+ 客户独有公式（同名覆盖内置）。流程的每一步就是调用其中一个公式。 */
import { onMounted, ref } from 'vue'

import { recipeApi } from '@/api'
import type { FormulaInfo } from '@/api/types'
import FunObject, { type FunKind } from '@/components/brand/FunObject.vue'
import BaseModal from '@/components/common/BaseModal.vue'
import { formulaArt } from '@/composables/chatModel'

const props = defineProps<{ customerId?: string }>()
const emit = defineEmits<{ close: [] }>()
const builtin = ref<FormulaInfo[]>([])
const custom = ref<FormulaInfo[]>([])
const open = ref<string | null>(null)

onMounted(async () => {
  const data = await recipeApi.formulas(props.customerId)
  builtin.value = data.builtin
  custom.value = data.customer
})

function groups(list: FormulaInfo[]) {
  const map = new Map<string, FormulaInfo[]>()
  for (const f of list) map.set(f.category, [...(map.get(f.category) ?? []), f])
  return [...map.entries()]
}
</script>

<template>
  <BaseModal title="公式库" width="max-w-4xl" @close="emit('close')">
    <p class="mb-4 text-sm text-mute">流程的每一步都是调用一个公式 + 填参数，执行时编译成 DuckDB SQL。客户独有的逻辑用「自定义处理（SQL）」或客户公式实现。</p>
    <div v-if="custom.length" class="mb-6">
      <div class="label-mini mb-2">客户独有公式</div>
      <div v-for="f in custom" :key="f.id" class="mb-2 rounded-xl border border-fun-purple/30 bg-fun-purple/5 p-3 text-sm">
        <div class="font-medium">{{ f.name }} <span class="font-mono text-xs text-faint">{{ f.id }} · v{{ f.version }}</span></div>
        <div class="text-xs text-mute">{{ f.intent }}</div>
        <pre v-if="f.sql" class="mt-2 overflow-auto rounded-lg bg-white p-2 font-mono text-[11px]">{{ f.sql }}</pre>
      </div>
    </div>
    <div v-for="[category, list] in groups(builtin)" :key="category" class="mb-5">
      <div class="label-mini mb-2">{{ category }}</div>
      <div class="grid gap-2 sm:grid-cols-2">
        <button v-for="f in list" :key="f.id" class="rounded-xl border border-line p-3 text-left transition hover:bg-paper" @click="open = open === f.id ? null : f.id">
          <div class="flex items-center gap-2">
            <FunObject :kind="formulaArt(f.id) as FunKind" :size="24" />
            <span class="text-sm font-medium">{{ f.name }}</span>
            <span class="ml-auto font-mono text-[10px] text-faint">{{ f.id }}</span>
          </div>
          <div class="mt-1 text-xs leading-relaxed text-mute">{{ f.intent }}</div>
          <ul v-if="open === f.id" class="mt-2 space-y-0.5 border-t border-line pt-2 text-[11px] text-mute">
            <li v-for="(p, name) in f.params" :key="name"><span class="font-mono text-ink">{{ name }}</span><span v-if="p.required" class="text-error">*</span>：{{ p.desc }}</li>
          </ul>
        </button>
      </div>
    </div>
  </BaseModal>
</template>

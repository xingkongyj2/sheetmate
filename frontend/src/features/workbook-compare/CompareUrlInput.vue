<script setup lang="ts">
/** 粘贴任意表格链接：支持本站表格浏览页链接，也支持任意公开的 .xlsx / .csv 链接（由后端代取）。 */
import { ArrowRight, Link2 } from '@lucide/vue'
import { ref } from 'vue'

import type { CompareSource } from './types'
import { parseCompareTargetInput } from './url'

const props = defineProps<{
  busy?: boolean
}>()

const emit = defineEmits<{ submit: [source: CompareSource] }>()

const text = ref('')
const error = ref('')

function submit() {
  if (props.busy) return
  const result = parseCompareTargetInput(text.value)
  if (!result.ok) {
    error.value = result.error
    return
  }
  error.value = ''
  emit('submit', result.source)
  text.value = ''
}
</script>

<template>
  <form class="url-input" @submit.prevent="submit" @keydown.esc.stop>
    <div class="flex items-center gap-1.5 text-[11px] font-medium text-faint">
      <Link2 class="size-3" />粘贴表格链接
    </div>
    <div class="mt-1.5 flex items-center gap-1.5">
      <input
        v-model="text"
        type="text"
        inputmode="url"
        autocomplete="off"
        spellcheck="false"
        class="url-field"
        aria-label="表格链接"
        @input="error = ''"
      >
      <button type="submit" class="url-go" :disabled="busy || !text.trim()" aria-label="加载链接中的表格" title="加载">
        <ArrowRight class="size-3.5" />
      </button>
    </div>
    <p v-if="error" role="alert" class="mt-1.5 text-[11px] leading-4 text-rose-600">{{ error }}</p>
  </form>
</template>

<style scoped>
.url-field {
  height: 32px;
  min-width: 0;
  flex: 1;
  border-radius: 9px;
  border: 1px solid var(--color-line);
  background: #fff;
  padding: 0 10px;
  font-size: 12px;
  color: var(--color-ink);
  outline: none;
  transition: border-color .15s, box-shadow .15s;
}
.url-field::placeholder {
  color: #a8a49d;
}
.url-field:focus {
  border-color: rgb(11 11 12 / 0.4);
  box-shadow: 0 0 0 3px rgb(11 11 12 / 0.06);
}
.url-go {
  display: inline-flex;
  height: 32px;
  width: 32px;
  flex: none;
  align-items: center;
  justify-content: center;
  border-radius: 9px;
  background: var(--color-ink);
  color: #fff;
  transition: opacity .15s;
}
.url-go:disabled {
  opacity: 0.3;
  cursor: default;
}
</style>

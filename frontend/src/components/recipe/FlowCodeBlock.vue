<script setup lang="ts">
import { Check, Copy } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

const props = withDefaults(defineProps<{ code: string; language?: 'python' | 'sql' | 'json'; title?: string }>(), { language: 'python' })
const SQL_KEYWORDS = /^(?:SELECT|FROM|WHERE|AS|WITH|JOIN|LEFT|RIGHT|INNER|OUTER|ON|GROUP|BY|ORDER|HAVING|LIMIT|CASE|WHEN|THEN|ELSE|END|DISTINCT|UNION|ALL|INSERT|INTO|CREATE|TABLE|NULL|TRUE|FALSE|AND|OR|NOT|IN|IS|OVER|PARTITION|ASC|DESC|CAST|SUM|COUNT|AVG|MIN|MAX)$/i
const copied = ref(false)
const copyError = ref(false)
watch(() => props.code, () => { copied.value = false; copyError.value = false })
const tokens = computed(() => {
  const pattern = props.language === 'python'
    ? /("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|#[^\n]*|\b(?:and|as|assert|async|await|break|class|continue|def|del|elif|else|except|False|finally|for|from|global|if|import|in|is|lambda|None|nonlocal|not|or|pass|raise|return|True|try|while|with|yield)\b|\b\d+(?:\.\d+)?\b)/g
    : /("(?:\\.|[^"\\])*"|'(?:''|[^'])*'|--[^\n]*|\b(?:SELECT|FROM|WHERE|AS|WITH|JOIN|LEFT|RIGHT|INNER|OUTER|ON|GROUP|BY|ORDER|HAVING|LIMIT|CASE|WHEN|THEN|ELSE|END|DISTINCT|UNION|ALL|INSERT|INTO|CREATE|TABLE|NULL|TRUE|FALSE|AND|OR|NOT|IN|IS|OVER|PARTITION|ASC|DESC|CAST|SUM|COUNT|AVG|MIN|MAX)\b|\b[A-Za-z_]\w*(?=\()|\b\d+(?:\.\d+)?\b)/gi
  return props.code.split(pattern).filter(Boolean).map((value, index) => ({
    key: index,
    value,
    style: /^(#|--)/.test(value) ? 'comment'
      : props.language === 'sql' && value.startsWith('"') ? 'ident'
      : /^["']/.test(value) ? 'string'
      : /^\d/.test(value) ? 'number'
      : props.language === 'sql' && /^[A-Za-z_]\w*$/.test(value) && !SQL_KEYWORDS.test(value) ? 'func'
      : /^\w+$/.test(value) ? 'keyword' : '',
  }))
})

/** 按行分组，每行自己带行号：长行换行时，行号仍然和它的第一行对齐 */
const rows = computed(() => {
  const out: { key: string; value: string; style: string }[][] = [[]]
  for (const token of tokens.value) {
    token.value.split('\n').forEach((part, index) => {
      if (index > 0) out.push([])
      if (part) out[out.length - 1]!.push({ key: `${token.key}:${index}`, value: part, style: token.style })
    })
  }
  return out
})

async function copy() {
  try { await navigator.clipboard.writeText(props.code); copied.value = true; copyError.value = false }
  catch { copyError.value = true }
}
</script>

<template>
  <div class="flow-code">
    <div class="flow-code-toolbar">
      <span class="min-w-0 truncate">{{ title || language.toUpperCase() }}</span>
      <button type="button" class="flow-code-copy" @click="copy"><Check v-if="copied" class="size-3.5" /><Copy v-else class="size-3.5" />{{ copied ? '已复制' : '复制' }}</button>
    </div>
    <div class="flow-code-scroll" role="group" :aria-label="`${language.toUpperCase()} 代码`">
      <div v-for="(row, index) in rows" :key="index" class="flow-code-row">
        <span class="flow-code-no" aria-hidden="true">{{ index + 1 }}</span>
        <code class="flow-code-text"><span v-for="token in row" :key="token.key" :class="token.style">{{ token.value }}</span></code>
      </div>
    </div>
    <p v-if="copyError" class="px-3 pb-2 text-xs text-[#f4cfa2]" role="status">请选中代码后复制。</p>
  </div>
</template>

<style scoped>
.flow-code { overflow: hidden; border: 1px solid #373a55; border-radius: 13px; background: #202333; color: #e3e6f3; }
.flow-code-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid #ffffff10; padding: 9px 12px; color: #babdd3; font: 11px/1.5 var(--font-sans, sans-serif); }
.flow-code-copy { display: inline-flex; flex: none; align-items: center; gap: 5px; border-radius: 5px; padding: 2px 4px; color: #d8d5ff; }
.flow-code-copy:hover { background: #6265dc35; }
.flow-code-copy:focus-visible { outline: 2px solid #aaa7ff; outline-offset: 2px; }
.flow-code-scroll { overflow: hidden; padding: 12px 0; font: 11.5px/1.8 var(--font-mono, monospace); tab-size: 4; }
.flow-code-row { display: flex; align-items: flex-start; }
.flow-code-no { width: 40px; flex: none; padding-right: 12px; color: #6d7388; text-align: right; user-select: none; }
.flow-code-text { min-width: 0; flex: 1; padding-right: 16px; font: inherit; white-space: pre-wrap; overflow-wrap: anywhere; }
.flow-code-text:empty::after { content: ' '; }
.keyword { color: #c5b4ff; font-weight: 600; }
.ident { color: #8fd3f4; }
.func { color: #f1b8d2; }
.string { color: #b5dec0; }
.number { color: #f1c595; }
.comment { color: #8993ad; font-style: italic; }
</style>

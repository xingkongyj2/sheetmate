<script setup lang="ts">
import { Check, Copy } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

const props = withDefaults(defineProps<{ code: string; language?: 'python' | 'sql' | 'json'; title?: string }>(), { language: 'python' })
const copied = ref(false)
const copyError = ref(false)
watch(() => props.code, () => { copied.value = false; copyError.value = false })
const lines = computed(() => props.code.split('\n').length)
const tokens = computed(() => {
  const pattern = props.language === 'python'
    ? /("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|#[^\n]*|\b(?:and|as|assert|async|await|break|class|continue|def|del|elif|else|except|False|finally|for|from|global|if|import|in|is|lambda|None|nonlocal|not|or|pass|raise|return|True|try|while|with|yield)\b|\b\d+(?:\.\d+)?\b)/g
    : /("(?:\\.|[^"\\])*"|'(?:''|[^'])*'|--[^\n]*|\b(?:SELECT|FROM|WHERE|AS|WITH|JOIN|LEFT|RIGHT|INNER|OUTER|ON|GROUP|BY|ORDER|HAVING|LIMIT|CASE|WHEN|THEN|ELSE|END|DISTINCT|UNION|ALL|INSERT|INTO|CREATE|TABLE|NULL|TRUE|FALSE|AND|OR|NOT|IN|IS|OVER|PARTITION|ASC|DESC|CAST|SUM|COUNT|AVG|MIN|MAX)\b|\b\d+(?:\.\d+)?\b)/gi
  return props.code.split(pattern).filter(Boolean).map((value, index) => ({
    key: index,
    value,
    style: /^(#|--)/.test(value) ? 'comment' : /^["']/.test(value) ? 'string' : /^\d/.test(value) ? 'number' : /^\w+$/.test(value) ? 'keyword' : '',
  }))
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
    <div class="flow-code-scroll" tabindex="0" :aria-label="`${language.toUpperCase()} 代码`">
      <div class="flow-code-lines" aria-hidden="true"><span v-for="line in lines" :key="line">{{ line }}</span></div>
      <pre><code><span v-for="token in tokens" :key="token.key" :class="token.style">{{ token.value }}</span></code></pre>
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
.flow-code-scroll { display: flex; max-height: 380px; overflow: auto; padding: 13px 0; font: 11.5px/1.8 var(--font-mono, monospace); tab-size: 4; }
.flow-code-lines { position: sticky; left: 0; flex: none; user-select: none; background: #202333; padding: 0 11px 0 13px; color: #73798e; text-align: right; }
.flow-code-lines span { display: block; }
pre { margin: 0; min-width: 0; padding-right: 16px; font: inherit; white-space: pre; }
.keyword { color: #c5b4ff; }
.string { color: #b5dec0; }
.number { color: #f1c595; }
.comment { color: #8993ad; font-style: italic; }
</style>

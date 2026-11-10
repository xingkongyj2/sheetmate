<script setup>
import { ref } from "vue"
import ExecutionPanel from "./src/components/chat/ExecutionPanel.vue"
const usage = (input, output, calls) => ({ prompt_tokens: input, completion_tokens: output, model_calls: calls, stages: { execution: { cached_tokens: 4096, reasoning_tokens: 1280 } } })
const makeMessage = (id, data) => ({ id, key: `db_${id}`, role: 'assistant', content: '处理完成', artifacts: [], timeline: [], streaming: false, meta: { execution: { status: 'completed', llm_usage: data } }, execution: { status: 'completed', llm_usage: data } })
const messages = [makeMessage(2, usage(18420, 2630, 5)), makeMessage(4, usage(32160, 6840, 8)), makeMessage(6, usage(42580, 5620, 9))]

const activeTab=ref("usage");const size=ref("wide");const scenario=ref("normal");const empty=makeMessage(8,undefined)
</script>
<template><main style="height:100vh;display:flex;align-items:center;justify-content:center;background:#f5f5f7;gap:40px"><div style="max-width:320px;padding:20px"><h1 style="font-size:24px;font-weight:600;margin-bottom:18px">消耗面板 · 视觉验收</h1><label style="display:block;margin-bottom:16px">宽度 <select v-model="size"><option value="wide">392 px</option><option value="narrow">320 px</option></select></label><label>数据 <select v-model="scenario"><option value="normal">正常记录</option><option value="empty">历史无记录</option></select></label></div><div :style="{ width: size === 'wide' ? '392px' : '320px', height: 'min(820px, 100vh)', background: 'white' }"><ExecutionPanel v-model:active-tab="activeTab" :detail="null" :messages="scenario === 'normal' ? messages : [empty]" :message="scenario === 'normal' ? messages[2] : empty" /></div></main></template>
<style>.execution-panel{position:relative!important;top:auto!important;right:auto!important;width:100%!important;box-shadow:none!important}.execution-backdrop{display:none!important}</style>
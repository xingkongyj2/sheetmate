<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { errorText } from '@/api/http'
import { getExecutionMetrics } from '@/api/observability'
import type { ExecutionMetrics } from '@/api/observability'

const days = ref(30)
const metrics = ref<ExecutionMetrics | null>(null)
const loading = ref(false)
const error = ref('')
let requestVersion = 0

async function load() {
  const version = ++requestVersion
  loading.value = true
  error.value = ''
  try {
    const result = await getExecutionMetrics(days.value)
    if (version === requestVersion) metrics.value = result
  } catch (err) {
    if (version === requestVersion) { metrics.value = null; error.value = errorText(err) }
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

onMounted(load)
const trend = computed(() => [...(metrics.value?.daily ?? [])].reverse())
const cachePartial = computed(() => !!metrics.value &&
  (metrics.value.usage.cached_tokens_missing_calls > 0 || metrics.value.usage.cache_miss_tokens_missing_calls > 0))
const integer = (value: number) => value.toLocaleString('zh-CN')
const percent = (value: number | null) => value === null ? '暂无数据' : `${(value * 100).toFixed(1)}%`
const duration = (value: number | null) => value === null ? '暂无数据' : value < 1000 ? `${Math.round(value)} 毫秒` : `${(value / 1000).toFixed(1)} 秒`
const labels: Record<string, string> = {
  completed: '完成', failed: '失败', incomplete: '未完成', cancelled: '已停止', interrupted: '待恢复',
  waiting: '等待处理', running: '进行中', other: '其他',
  tool_timeout: '工具超时', task_timeout: '任务超时', model_timeout: '模型超时', request_timeout: '请求超时',
  model_call_budget: '模型调用达上限', prompt_token_budget: '输入预算达上限', token_budget: 'Token 预算达上限',
  input_budget: '单次输入过长', tool_budget: '工具调用达上限', execution_error: '执行异常', tool_error: '工具失败',
  missing_tool_result: '工具结果缺失', missing_completion: '缺少完整答复', missing_delivery: '缺少交付文件',
  delivery_unverified: '交付核对未通过', completion_unverified: '完成核对未通过', model_error: '模型请求失败',
  protocol_error: '调用格式错误', tool_protocol: '工具调用格式错误', loop_detected: '重复执行',
  max_iterations: '执行轮数达上限', watchdog_timeout: '执行超时', recovery_blocked: '恢复受阻', recovery_expired: '恢复已过期',
  chat: '对话', execution: '执行', structured: '结构化判断', request_intent: '需求识别', execution_plan: '任务规划',
  plan_review: '计划核对', plan_repair: '计划调整', step_verdict: '步骤核对', answer_audit: '答复核对',
  task_completion: '完成核对', final_delivery: '交付核对', recipe: '流程生成', memory_extraction: '记忆提取',
  title: '对话标题', history_compaction: '历史压缩', customer_summary: '客户总结',
  recipe_persistence: '流程保存判断', recipe_deletion: '流程删除判断',
}
</script>

<template>
  <section class="mt-10" aria-labelledby="execution-metrics-heading" :aria-busy="loading">
    <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
      <h2 id="execution-metrics-heading" class="text-[16px] font-semibold">运行统计</h2>
      <div class="flex items-center gap-2">
        <select v-model.number="days" aria-label="统计时间范围" class="field !w-auto !py-1.5 text-xs" :disabled="loading" @change="load">
          <option :value="7">近 7 天</option><option :value="30">近 30 天</option><option :value="90">近 90 天</option>
        </select>
        <button type="button" class="btn-line text-xs" :disabled="loading" @click="load">{{ loading ? '加载中…' : '刷新统计' }}</button>
      </div>
    </div>
    <div v-if="error" class="card-soft p-5 text-sm text-error" role="alert">统计加载失败：{{ error }}</div>
    <div v-else-if="metrics" class="card-soft p-5">
      <p class="mb-4 text-xs leading-relaxed text-mute">仅统计你在当前有权限会话中的持久任务；恢复执行仍算一次。历史版本未记录的任务不计入。</p>
      <p v-if="metrics.coverage.truncated" class="mb-4 text-xs text-warning">范围内任务较多，仅统计最近 {{ integer(metrics.coverage.limit) }} 次。</p>
      <div class="grid grid-cols-2 gap-5 sm:grid-cols-4">
        <div><p class="text-xs text-mute">任务数</p><p class="mt-1 text-xl font-semibold">{{ integer(metrics.tasks) }}</p></div>
        <div><p class="text-xs text-mute">成功率</p><p class="mt-1 text-xl font-semibold">{{ percent(metrics.success_rate) }}</p></div>
        <div><p class="text-xs text-mute">耗时中位数</p><p class="mt-1 text-xl font-semibold">{{ duration(metrics.latency.p50_ms) }}</p></div>
        <div><p class="text-xs text-mute">95% 任务耗时不超过</p><p class="mt-1 text-xl font-semibold">{{ duration(metrics.latency.p95_ms) }}</p></div>
      </div>
      <p class="mt-3 text-xs text-mute">成功率按完成、失败及未完成任务计算，共 {{ integer(metrics.success_rate_tasks) }} 次；等待、待恢复与主动停止单列。耗时不计应用关闭期间。</p>
      <div class="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-xs">
        <span v-for="(count, status) in metrics.statuses" :key="status">{{ labels[status] ?? status }} {{ integer(count) }}</span>
        <span>曾恢复 {{ integer(metrics.recovered_tasks) }}</span>
      </div>
      <div class="mt-5 border-t border-line pt-4 text-sm">
        <p>模型调用 {{ integer(metrics.usage.model_calls) }} 次 · 输入 {{ integer(metrics.usage.prompt_tokens) }} / 输出 {{ integer(metrics.usage.completion_tokens) }} Token</p>
        <p class="mt-2">已记录缓存命中 {{ integer(metrics.usage.cached_tokens) }} Token · 命中率 {{ cachePartial ? '未知（部分记录）' : percent(metrics.usage.cache_hit_rate) }}</p>
        <p v-if="metrics.usage.usage_missing_calls || metrics.usage.missing_tasks || metrics.usage.pending_tasks" class="mt-2 text-xs text-mute">用量记录不完整：{{ integer(metrics.usage.usage_missing_calls) }} 次调用缺少服务商用量，{{ integer(metrics.usage.missing_tasks) }} 个任务缺少快照，{{ integer(metrics.usage.pending_tasks) }} 个任务仍在更新。以上 Token 为已上报部分。</p>
      </div>
      <p v-if="metrics.tasks === 0" class="mt-5 text-sm text-mute">这段时间还没有任务记录。</p>
      <template v-else>
        <details class="mt-5 text-sm">
          <summary class="cursor-pointer font-medium">每日趋势</summary>
          <div class="mt-3 max-h-64 overflow-auto">
            <table class="w-full text-left text-xs"><thead><tr><th class="pb-2">日期</th><th>任务</th><th>完成</th><th>失败 / 未完成</th><th>Token</th></tr></thead>
              <tbody><tr v-for="day in trend" :key="day.date"><td class="py-1.5">{{ day.date }}</td><td>{{ day.tasks }}</td><td>{{ day.completed }}</td><td>{{ day.failed }}</td><td>{{ integer(day.prompt_tokens + day.completion_tokens) }}</td></tr></tbody>
            </table>
          </div>
        </details>
        <details v-if="metrics.failures.length" class="mt-4 text-sm">
          <summary class="cursor-pointer font-medium">失败与未完成原因</summary>
          <ul class="mt-3 space-y-2 text-xs"><li v-for="item in metrics.failures" :key="item.code">{{ labels[item.code] ?? '其他' }}：{{ item.tasks }} 次</li></ul>
        </details>
        <details v-if="metrics.tools.length" class="mt-4 text-sm">
          <summary class="cursor-pointer font-medium">工具执行</summary>
          <p class="mt-2 text-xs text-mute">平均耗时仅计算有完整记录且未恢复的调用。</p>
          <div class="mt-3 overflow-x-auto"><table class="w-full text-left text-xs"><thead><tr><th class="pb-2">工具</th><th>次数</th><th>失败</th><th>待完成</th><th>平均耗时</th></tr></thead>
            <tbody><tr v-for="tool in metrics.tools" :key="tool.tool"><td class="py-1.5">{{ tool.tool }}</td><td>{{ tool.calls }}</td><td>{{ tool.failed }}</td><td>{{ tool.pending }}</td><td>{{ duration(tool.average_duration_ms) }}</td></tr></tbody>
          </table></div>
        </details>
        <details v-if="metrics.stages.length" class="mt-4 text-sm">
          <summary class="cursor-pointer font-medium">模型调用阶段</summary>
          <ul class="mt-3 space-y-2 text-xs"><li v-for="stage in metrics.stages" :key="stage.stage">{{ labels[stage.stage] ?? '其他' }}：{{ stage.model_calls }} 次 · {{ integer(stage.prompt_tokens + stage.completion_tokens) }} Token</li></ul>
        </details>
      </template>
    </div>
    <p v-else class="text-sm text-mute" role="status">正在加载运行统计…</p>
  </section>
</template>

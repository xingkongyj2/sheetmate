/** 统计页用到的中文标签与数字格式化（纯函数，不依赖其他模块，便于单独测试）。 */

/** 状态 / 失败原因 / 模型阶段的中文名；未收录的一律显示「其他」，不把内部代码露给用户。 */
export const LABELS: Record<string, string> = {
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

export const label = (code: string): string => LABELS[code] ?? '其他'
export const integer = (value: number): string => value.toLocaleString('zh-CN')

/** 大数字缩写：1.2 万、3.4 亿；小于一万按原样显示。 */
export function compact(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 1e8) return `${trim(value / 1e8)} 亿`
  if (abs >= 1e4) return `${trim(value / 1e4)} 万`
  return integer(value)
}
const trim = (value: number): string => {
  const abs = Math.abs(value)
  const text = value.toFixed(abs >= 100 ? 0 : abs >= 10 ? 1 : 2)
  return text.includes('.') ? text.replace(/\.?0+$/, '') : text  // 只去掉小数部分的 0，不动整数
}

export const percent = (value: number | null): string => value === null ? '—' : `${(value * 100).toFixed(1)}%`

export function duration(value: number | null): string {
  if (value === null) return '—'
  if (value < 1000) return `${Math.round(value)} 毫秒`
  const seconds = value / 1000
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`
  const minutes = Math.floor(seconds / 60)
  return minutes < 60 ? `${minutes}分${Math.round(seconds % 60)}秒` : `${Math.floor(minutes / 60)}小时${minutes % 60}分`
}

/** 2026-11-13 → 11-13 */
export const shortDate = (iso: string): string => iso.slice(5)

/**
 * 纵轴刻度：步长取 1 / 2 / 5 × 10ⁿ 且至少为 1（统计的都是个数，不会出现 0.5），从 0 起，
 * 刻度数按最大值实际需要来（最大 12 → 0 / 5 / 10 / 15），最多约 `ticks` 段。
 */
export function niceScale(max: number, ticks = 4): { max: number; ticks: number[] } {
  if (!(max > 0)) return { max: ticks, ticks: Array.from({ length: ticks + 1 }, (_, i) => i) }
  const rough = max / ticks
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  const step = Math.max(1, [1, 2, 5, 10].map((m) => m * magnitude).find((candidate) => candidate >= rough) ?? Math.ceil(rough))
  const count = Math.ceil(max / step)
  return { max: step * count, ticks: Array.from({ length: count + 1 }, (_, i) => step * i) }
}

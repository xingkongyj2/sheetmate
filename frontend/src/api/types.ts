/** 后端接口的数据类型（与 backend/sheetmate/services/*.py 的返回结构一致） */

export interface User {
  id: number
  account: string
  display_name: string
  role: 'admin' | 'member'
  profile: Record<string, unknown>
  onboarded: boolean
}

export interface ColumnBrief {
  name: string
  dtype: string
}

export interface SheetBrief {
  sheet: string
  rows: number
  cols: number
  header?: { header_row: number; header_rows: number; title: string }
  columns: ColumnBrief[]
}

export interface FileInfo {
  file_id: string
  filename: string
  kind: 'input' | 'expected' | 'output' | 'doc'
  kind_label: string
  size: number
  customer_id: string | null
  conversation_id: string | null
  run_id: string | null
  created_at: string | null
  sheets: SheetBrief[]
  download_url: string
}

export interface FlowStep {
  id: number
  name: string
  action: string
  formula: string
  formula_kind: 'builtin' | 'customer'
  description: string
  output: string
  inputs: string[]
}

export interface RecipeFlowData {
  inputs: { alias: string; description: string; file_pattern: string }[]
  steps: FlowStep[]
  outputs: { name: string; source: string }[]
}

export interface Verification {
  status: 'pass' | 'warn' | 'fail'
  match_rate: number
  summary: string
  informational?: boolean // 修订后的流程：和原人工样例的对比只作参考
}

export interface RecipeBrief {
  recipe_id: string
  version: number
  name: string
  description: string
  status: 'draft' | 'active' | 'deprecated'
  status_label: string
  customer_id: string | null
  conversation_id: string | null
  inputs: string[]
  input_patterns: { alias: string; file_pattern: string; description: string }[]
  outputs: string[]
  step_count: number
  steps: { id: number; name: string; formula: string; formula_kind: string; description: string }[]
  verification: Verification | null
  last_run: { run_id: string; status: string } | null
  created_by: string
  updated_at: string | null
  flow?: RecipeFlowData
  recipe?: RecipeJSON
  match?: string
}

export interface RecipeStepJSON {
  id: number
  name: string
  action: string
  formula: string
  formula_kind: 'builtin' | 'customer'
  params: Record<string, unknown>
  output: string
  description: string
  explanation: string
  confidence: number
  sql: string
  assertions: { type: string; params: Record<string, unknown>; severity: string }[]
  enabled: boolean
}

export interface RecipeJSON {
  id: string
  version: number
  name: string
  description: string
  status: string
  customer_id: string | null
  inputs: {
    alias: string
    description: string
    file_pattern: string
    sheet: string | null
    expected_columns: string[]
    required_columns: string[]
    key_columns: string[]
  }[]
  field_mapping: Record<string, string[]>
  steps: RecipeStepJSON[]
  output_spec: { name: string; source: string; description: string; columns?: string[] | null; split_by?: string | null }[]
  validation_rules: { id: string; type: string; description: string; params: Record<string, unknown>; severity: string }[]
  metadata: Record<string, unknown>
  learned_from: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface CustomerCard {
  id: string
  name: string
  code: string
  mascot: string
  description: string
  excerpt: string
  file_count: number
  recipe_count: number
  conversation_count: number
  recipes: { recipe_id: string; name: string; version: number; status: string; steps: number }[]
  last_activity: string
  has_summary: boolean
}

export interface MemoryItem {
  id: number
  category: string
  category_label: string
  key: string
  value: string
  scope_label: string
  source: string
  updated_at: string
}

export interface RunBrief {
  run_id: string
  kind: 'analysis' | 'modify' | 'replay' | 'create' | 'script'
  kind_label: string
  status: string
  status_label: string
  customer_id: string | null
  conversation_id: string | null
  recipe_id: string | null
  recipe_version: number | null
  request_text: string
  tool_sequence: string
  error: string
  summary: string
  output_file_id: string | null
  duration_ms: number
  created_at: string
  finished_at: string | null
  verification: { status: string; overall_match_rate: number; summary: string } | null
}

export interface RunDetail extends RunBrief {
  steps: StepReport[]
  validation: Record<string, unknown> & { checks?: ValidationCheck[]; summary?: string; status?: string }
  verification_detail: Record<string, unknown>
}

export interface CustomerDetail extends Omit<CustomerCard, 'recipes'> {
  summary: string
  summary_by: 'ai' | 'human' // ai：流程变化时自动更新；human：手动编辑过，不再自动覆盖
  summary_updated_at: string | null
  output_count: number
  files: FileInfo[]
  outputs: FileInfo[]
  recipes: (RecipeBrief & { flow: RecipeFlowData; recipe: RecipeJSON })[]
  memories: MemoryItem[]
  field_mapping: Record<string, string[]>
  formulas: CustomerFormula[]
  runs: RunBrief[]
  conversations: { id: string; title: string; mode: string; updated_at: string }[]
}

export interface FormulaParam {
  type: string
  required: boolean
  default: unknown
  desc: string
  values: unknown[] | null
  of?: string | null // column / columns 属于哪个表参数
  item_type?: string | null
}

export interface FormulaInfo {
  id: string
  name: string
  category: string
  intent: string
  kind: 'builtin' | 'customer'
  kind_label: string
  version: number
  status: string
  params: Record<string, FormulaParam>
  sql: string | null
  example: Record<string, unknown> | null
  triggers?: string[]
  overrides_builtin?: boolean
}

/** 公式被哪个流程的哪几步用到；problems 是这些步骤现在会遇到的问题（比如公式改了参数） */
export interface FormulaUsage {
  recipe_id: string
  name: string
  version: number
  status: string
  steps: { id: number; name: string }[]
  problems: string[]
}

export interface CustomerFormula extends FormulaInfo {
  status_label: string
  overrides_name: string | null // 同 id 覆盖了哪个内置公式
  used_by: FormulaUsage[]
  created_by: string
  updated_at: string | null
}

export interface FormulaGenerationResult {
  formula: Record<string, unknown>
  explanation: string
  warnings: string[]
  questions: string[]
}

/** 试运行的结果：failed 不是接口出错，stage 说明在哪一步出的问题 */
export interface FormulaTrialResult {
  status: 'success' | 'failed'
  stage?: 'definition' | 'data' | 'params' | 'run'
  error?: string
  sql?: string
  rows?: number
  columns?: { name: string; type: string }[]
  preview?: { total: number; rows: unknown[][]; columns: { name: string; type: string }[] }
  params?: Record<string, unknown>
  warnings?: string[]
  rows_in?: Record<string, number>
  duration_ms?: number
  inputs?: { table: string; source: 'file' | 'text'; filename: string; sheet: string; renamed: Record<string, string>; rows: number; columns: string[] }[]
}

export interface FormulaTrialInput {
  table: string
  file_id?: string
  sheet?: string
  text?: string
}

export interface TableBrief {
  name: string
  label: string
  rows: number
  cols: number
  columns: string[]
  source: 'uploaded' | 'computed'
  role: 'input' | 'expected' | 'derived' | 'result'
  file_id?: string | null
  description?: string
}

export interface TablePage {
  name: string
  label: string
  total: number
  offset: number
  limit: number
  columns: { name: string; type: string }[]
  rows: unknown[][]
}

export interface ConversationBrief {
  id: string
  title: string
  mode: 'analyze' | 'modify' | 'create'
  mode_label: string
  customer: { id: string; name: string; mascot: string } | null
  created_at: string
  updated_at: string
  running?: boolean
}

export interface StoredMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  events: AgentEvent[]
  meta: Record<string, unknown>
  created_at: string
}

export interface ConversationDetail extends ConversationBrief {
  messages: StoredMessage[]
  tables: TableBrief[]
  files: FileInfo[]
  recipes: RecipeBrief[]
  runs: { run_id: string; kind: string; status: string; created_at: string }[]
}

export interface ValidationCheck {
  rule_id: string
  rule_type: string
  description: string
  status: 'pass' | 'warn' | 'fail' | 'info'
  severity: string
  expected?: unknown
  actual?: unknown
  message: string
}

export interface StepReport {
  step_id: number
  name: string
  formula: string
  formula_name: string
  formula_kind?: string
  description: string
  status: 'success' | 'warning' | 'repaired' | 'failed'
  output: string
  rows_in?: Record<string, number>
  rows_out?: number
  columns?: string[]
  metrics?: Record<string, unknown> & { match_rate?: number; matched_rows?: number; total_rows?: number }
  warnings?: string[]
  attempts?: { attempt: number; formula: string; error: string; explanation?: string }[]
  duration_ms: number
  preview?: { columns: string[]; rows: unknown[][]; total: number }
  sql?: string
  params?: Record<string, unknown>
  error?: string
}

export interface VerifyReport {
  status: 'pass' | 'warn' | 'fail'
  overall_match_rate: number
  summary: string
  informational?: boolean
  details: string[]
  sheets: {
    sheet: string
    status: string
    match_rate: number
    messages: string[]
    mismatches?: { column: string; expected: unknown; actual: unknown; key?: unknown; row?: number }[]
  }[]
}

export interface DiffCard {
  output_sheet: string
  input_table: string
  rows: [number, number]
  cols: [number, number]
  added: string[]
  removed: string[]
  renamed: Record<string, string>
  highlights: string[]
}

export interface PlanStep {
  id: number
  description: string
}

/** Agent 事件（SSE），字段随 type 变化 */
export interface AgentEvent {
  type: string
  [key: string]: any // eslint-disable-line @typescript-eslint/no-explicit-any
}

export interface LLMConfig {
  configured: boolean
  base_url: string
  model: string
  key_hint: string
  default_model: string
  providers: { id: string; name: string; base_url: string; models: string[] }[]
}

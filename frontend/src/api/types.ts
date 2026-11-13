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
  media_type?: string
  preview_url?: string | null
  intermediate?: boolean
  delivery_status?: 'candidate' | 'final' | 'intermediate' | 'superseded'
  superseded_by_file_ids?: string[]
  step_id?: number | null
  step_name?: string | null
}

export type RecipeToolKind = 'formula' | 'sql' | 'python'
export type RecipeInputKind = 'table' | 'file'

export interface ScriptReference {
  script_id: string
  script_title: string
  script_filename: string
}

export interface ScriptFile extends ScriptReference {
  code: string
}

export interface RecipeArtifact {
  step_id: number
  ref: string
  name: string
  path?: string
  file_id?: string
  filename?: string
  download_url?: string
  delivery_status?: 'candidate' | 'final' | 'intermediate' | 'superseded'
  intermediate?: boolean
}

export interface FlowStep {
  id: number
  name: string
  action: string
  formula: string
  kind?: RecipeToolKind
  formula_kind: 'builtin' | 'customer'
  description: string
  output: string
  inputs: string[]
  params?: Record<string, unknown>
  script_id?: string
  script_title?: string
  script_filename?: string
}

export interface RecipeFlowData {
  inputs: { alias: string; kind?: RecipeInputKind; description: string; file_pattern: string; source_file?: string; sheet?: string | null }[]
  input_files?: { name: string; aliases: string[]; file_pattern: string; file_id?: string }[]
  steps: FlowStep[]
  outputs: { name: string; source: string }[]
  output_files?: { name: string; ref: string; source: string; sources: string[]; file_id?: string; download_url?: string }[]
}

export interface RecipeEditRequest {
  recipe_id: string
  version: number
  target: {
    kind: 'workflow' | 'input' | 'output' | 'step'
    aliases?: string[]
    name?: string
    source?: string
    sources?: string[]
    step_id?: number
  }
  operation: 'modify' | 'add' | 'remove' | 'replace'
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
  customer_id: string | null
  conversation_id: string | null
  inputs: string[]
  input_patterns: { alias: string; kind?: RecipeInputKind; file_pattern: string; description: string }[]
  outputs: string[]
  step_count: number
  steps: ({ id: number; name: string; formula: string; kind?: RecipeToolKind; formula_kind: string; description: string } & Partial<ScriptReference>)[]
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
  kind?: RecipeToolKind
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
  customer_id: string | null
  inputs: {
    alias: string
    kind?: RecipeInputKind
    description: string
    file_pattern: string
    source_file?: string
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
  recipes: { recipe_id: string; name: string; version: number; steps: number }[]
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
  recipe_id?: string | null
  execution_evidence?: { kind?: string; verified?: boolean }
  updated_at: string
}

export interface FieldMappingEntry {
  standard: string
  aliases: string[]
  source: 'ai' | 'human'
}

export interface CustomerWorkflow extends RecipeBrief {
  flow: RecipeFlowData
  recipe: RecipeJSON
  summary: string
  summary_by: 'ai' | 'human'
  summary_updated_at: string | null
  files: FileInfo[]
  output_files: FileInfo[]
  runs: RunDetail[]
  memories: MemoryItem[]
  field_mapping: Record<string, string[]>
  field_mapping_entries: FieldMappingEntry[]
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
  validation: Record<string, unknown> & { checks?: ValidationCheck[]; summary?: string; status?: string; files?: RecipeArtifact[] }
  verification_detail: Record<string, unknown>
}

export interface CustomerDetail extends Omit<CustomerCard, 'recipes'> {
  primary_recipe_id: string | null
  summary: string
  summary_by: 'ai' | 'human' // ai：流程变化时自动更新；human：手动编辑过，不再自动覆盖
  summary_updated_at: string | null
  output_count: number
  files: FileInfo[]
  outputs: FileInfo[]
  recipes: CustomerWorkflow[]
  memories: MemoryItem[]
  field_mapping: Record<string, string[]>
  field_mapping_entries?: FieldMappingEntry[]
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
  filename?: string
  sheet?: string | null
  rows: number
  cols: number
  columns: string[]
  source: 'uploaded' | 'computed'
  role: 'input' | 'expected' | 'derived' | 'result'
  file_id?: string | null
  description?: string
  conversation_id?: string | null
  conversation_title?: string
  run_id?: string | null
  run_title?: string
  created_at?: string | null
  preview_only?: boolean
  preview?: unknown[][]
  snapshot_id?: string
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
  recipe_id?: string | null
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
  running_job: { id: string; message_id: number } | null
  execution?: {
    can_resume: boolean
    execution_id?: string
    assistant_message_id?: number | null
    status: ExecutionStatus
    resume_reason?: string
  }
  pending_steers?: RunSteer[]
  messages: StoredMessage[]
  tables: TableBrief[]
  files: FileInfo[]
  recipes: RecipeBrief[]
  runs: { run_id: string; kind: string; status: string; created_at: string }[]
}

export interface RunSteer {
  message_id: number
  content: string
  status: 'queued' | 'committed' | 'withdrawn'
  job_id: string
  execution_id?: string
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
  kind?: RecipeToolKind
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
  stdout?: string
  stderr?: string
  checks?: { message: string; ok: boolean }[]
  result?: Record<string, unknown>
  output_files?: RecipeArtifact[]
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
export interface PublicOperationDetail {
  label: string
  items: string[]
  ordered?: boolean
}

export type ExecutionStatus = 'running' | 'completed' | 'incomplete' | 'waiting' | 'failed' | 'cancelled' | 'interrupted' | 'unknown'

/** 整次任务的状态；与一次模型调用或单个步骤结束分开保存。 */
export interface ExecutionState {
  status: ExecutionStatus
  /** 当前回复开始时的 Unix 时间戳（毫秒）；继续任务时重新开始。 */
  started_at?: number
  duration_ms?: number
  code?: string
  summary?: string
  finish_reason?: string | null
  last_step?: string | Record<string, unknown> | null
  model?: string
  iteration?: number
  iteration_limit?: number
  tool_calls_used?: number
  tool_calls_limit?: number
  [key: string]: unknown
}

export interface ModelCallEvent extends AgentEvent {
  type: 'model_call_start' | 'model_call_done'
  call_id: string
  model?: string
  iteration: number
  iteration_limit: number
  tool_calls_used: number
  tool_calls_limit: number
  status?: 'ok' | 'error' | 'cancelled'
  code?: string
  summary?: string
  finish_reason?: string | null
  duration_ms?: number
}

export interface ExecutionEvent extends AgentEvent, ExecutionState {
  type: 'execution_stop' | 'execution_status'
}

export interface AgentEvent {
  /** Whether the exact recipe definition used for this run was explicitly saved. */
  recipe_persisted?: boolean
  type: string
  details?: PublicOperationDetail[]
  [key: string]: any // eslint-disable-line @typescript-eslint/no-explicit-any
}

export interface DeliveryManifestEvent extends AgentEvent {
  type: 'delivery_manifest'
  candidate_file_ids: string[]
  final_file_ids: string[]
  replaced_file_ids?: string[]
  files?: FileInfo[]
  status: 'confirmed' | 'unverified' | 'incomplete'
  summary: string
}

export type LLMProviderId = 'deepseek' | 'qwen' | 'custom'

export interface LLMCapabilities {
  structured_output: 'auto' | 'json_schema' | 'json_object' | 'prompt'
  thinking_protocol: 'auto' | 'deepseek' | 'qwen' | 'none'
}

export interface LLMRuntimePolicy {
  compatibility_mode: 'relaxed'
  stream_recovery_retries: number
  budget_enabled: boolean
  structured_retries: number
  tool_protocol_retries: number
  sdk_retries: number
  structured_timeout: number
  context_target_chars: number
  context_window_tokens: number
  max_model_calls: number
  max_prompt_tokens: number
  max_total_tokens: number
  max_task_seconds: number
  max_input_chars: number
  auxiliary_thinking_enabled: boolean
  llm_soft_idle_seconds: number
  llm_hard_idle_seconds: number
  stream_idle_seconds: number
  tool_timeout_seconds: number
  cancellation_grace_seconds: number
}

export interface LLMAdvancedConfig {
  timeout: number
  /** 输出 Token 上限，仅在 max_tokens_enabled 为 true 时生效 */
  max_tokens: number
  /** 关闭时请求不带输出上限，不额外限制 */
  max_tokens_enabled: boolean
  capabilities: LLMCapabilities
  runtime_policy: LLMRuntimePolicy
}

export interface LLMConfigUpdate {
  provider_id: LLMProviderId
  enabled?: boolean
  api_key?: string
  base_url?: string
  model?: string
  thinking_enabled?: boolean
  timeout?: number
  max_tokens?: number
  max_tokens_enabled?: boolean
  capabilities?: Partial<LLMCapabilities>
  runtime_policy?: Partial<LLMRuntimePolicy>
}

export interface LLMProviderConfig extends LLMAdvancedConfig {
  id: LLMProviderId
  enabled: boolean
  name: string
  description: string
  base_url: string
  models: string[]
  thinking_models?: string[]
  thinking_model_aliases?: string[]
  configured: boolean
  api_key: string
  model: string
  thinking_supported: boolean
  thinking_enabled: boolean
}

export interface LLMConfig extends LLMAdvancedConfig {
  configured: boolean
  base_url: string
  model: string
  thinking_enabled: boolean
  thinking_supported: boolean
  key_hint: string
  default_model: string
  active_provider_id: LLMProviderId | null
  providers: LLMProviderConfig[]
}

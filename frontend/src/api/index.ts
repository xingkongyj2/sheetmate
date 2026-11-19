/**
 * 后端接口封装（按资源分组）。所有函数返回解析好的数据，出错抛 ApiError。
 */
import { http } from './http'
import { parseLLMConfig } from './llmConfig'
import { getStream, postStream } from './sse'
import type {
  AgentEvent,
  ConversationBrief,
  ConversationDetail,
  CustomerCard,
  CustomerDetail,
  CustomerFormula,
  FileInfo,
  FormulaGenerationResult,
  FormulaInfo,
  FormulaSuggestion,
  FormulaTrialInput,
  FormulaTrialResult,
  LLMConfigUpdate,
  MemoryItem,
  PrivacyCopyPreview,
  PrivacyColumnChange,
  PrivacyColumns,
  PrivacyFileStatus,
  PrivacyOverview, PrivacyBlock, PrivacyRestore, PrivacySend,
  RecipeBrief,
  RecipeEditRequest,
  PrivacyRuleSetting,
  RecipeFlowData,
  RecipeJSON,
  RunBrief,
  RunDetail,
  ScriptFile,
  TableBrief,
  TablePage,
  User,
} from './types'

export const scriptApi = {
  get: async (id: string) =>
    (await http.get<{ script: ScriptFile }>(`/scripts/${encodeURIComponent(id)}`)).data.script,
}

// ---------------------------------------------------------------- 认证
export const authApi = {
  me: async () => (await http.get<{ user: User }>('/auth/me')).data.user,
  login: async (account: string, password: string) =>
    (await http.post<{ user: User }>('/auth/login', { account, password })).data.user,
  register: async (account: string, password: string, display_name = '') =>
    (await http.post<{ user: User }>('/auth/register', { account, password, display_name })).data.user,
  logout: async () => http.post('/auth/logout'),
  updateProfile: async (payload: { display_name?: string; avatar?: string; profile?: Record<string, unknown> }) =>
    (await http.put<{ user: User }>('/auth/profile', payload)).data.user,
  changePassword: async (current_password: string, new_password: string) =>
    http.put('/auth/password', { current_password, new_password }),
}

// ---------------------------------------------------------------- 设置
export const settingsApi = {
  getLLM: async () => parseLLMConfig((await http.get<unknown>('/settings/llm')).data),
  saveLLM: async (payload: LLMConfigUpdate) =>
    parseLLMConfig((await http.put<unknown>('/settings/llm', payload)).data),
  testLLM: async (payload: LLMConfigUpdate) =>
    (await http.post<{ ok: boolean; reply?: string; model?: string; error?: { message: string } }>('/settings/llm/test', payload,
      // 后端测试连接最多等 300 秒，这里多留 10 秒接收超时说明，避免被前端的默认超时提前中断。
      { timeout: 310_000, validateStatus: () => true })).data,
  // 脱敏设置：总开关和每条规则的开关
  getPrivacy: async () => (await http.get<{ enabled: boolean; rules: PrivacyRuleSetting[] }>('/settings/privacy')).data,
  savePrivacy: async (payload: { enabled?: boolean; rules?: Record<string, boolean> }) =>
    (await http.put<{ enabled: boolean; rules: PrivacyRuleSetting[] }>('/settings/privacy', payload)).data,
  // 脱敏模型：与对话模型同构的一套独立配置，只给脱敏模块用
  getPrivacyLLM: async () => parseLLMConfig((await http.get<unknown>('/settings/privacy-llm')).data),
  savePrivacyLLM: async (payload: LLMConfigUpdate) =>
    parseLLMConfig((await http.put<unknown>('/settings/privacy-llm', payload)).data),
  testPrivacyLLM: async (payload: LLMConfigUpdate) =>
    (await http.post<{ ok: boolean; reply?: string; model?: string; error?: { message: string } }>('/settings/privacy-llm/test', payload,
      { timeout: 310_000, validateStatus: () => true })).data,
}

// ---------------------------------------------------------------- 客户
export const customerApi = {
  list: async () => (await http.get<{ customers: CustomerCard[] }>('/customers')).data.customers,
  get: async (id: string) => (await http.get<{ customer: CustomerDetail }>(`/customers/${id}`)).data.customer,
  create: async (payload: { name: string; code?: string; description?: string }) =>
    (await http.post<{ customer: CustomerCard }>('/customers', payload)).data.customer,
  update: async (id: string, payload: Partial<{ name: string; code: string; description: string; mascot: string; primary_recipe_id: string }>) =>
    (await http.put<{ customer: CustomerCard }>(`/customers/${id}`, payload)).data.customer,
  remove: async (id: string) => http.delete(`/customers/${id}`),
  // 模型调用由后端的等待和重试策略控制，前端不另用短时限截断。
  refreshSummary: async (id: string, recipeId?: string) =>
    (await http.post<{ summary: string; by_ai: boolean }>(`/customers/${id}/summary`, {}, { params: { recipe_id: recipeId }, timeout: 0 })).data,
  upgradeSummary: async (id: string, recipeId?: string) =>
    (await http.post<{ summary: string; updated: boolean; summary_by: 'ai' | 'human' }>(
      `/customers/${id}/summary`, {}, { params: { only_if_outdated: true, recipe_id: recipeId }, timeout: 0 },
    )).data,
  uploadFile: async (id: string, file: File, kind = 'input', recipeId?: string, forRun = false) => {
    const form = new FormData()
    form.append('file', file)
    form.append('kind', kind)
    return (await http.post<{ file: FileInfo }>(`/customers/${id}/files`, form, { params: { recipe_id: recipeId, for_run: forRun || undefined } })).data.file
  },
  saveMapping: async (id: string, mapping: Record<string, string[]>, recipeId?: string) =>
    (await http.put<{ mapping: Record<string, string[]> }>(`/customers/${id}/field-mapping`, { mapping }, { params: { recipe_id: recipeId } })).data.mapping,
  addMemory: async (id: string, payload: { value: string; category?: string; key?: string }, recipeId?: string) =>
    (await http.post<{ memory: MemoryItem }>(`/customers/${id}/memories`, payload, { params: { recipe_id: recipeId } })).data.memory,

  // ---- 客户独有公式（参数声明 + {{参数}} SQL 模板）
  formulas: async (id: string) => (await http.get<{ formulas: CustomerFormula[] }>(`/customers/${id}/formulas`)).data.formulas,
  generateFormula: async (id: string, payload: { description: string; file_ids?: string[]; tables?: { file_id: string; sheet?: string }[]; formula?: Record<string, unknown> }) =>
    (await http.post<FormulaGenerationResult>(`/customers/${id}/formulas/generate`, payload, { timeout: 0 })).data,
  /** 新增 / 升级：同一个 id 每保存一次升一个版本。返回保存后的公式，used_by[].problems 是它让哪些流程的哪一步出了问题 */
  saveFormula: async (id: string, body: Record<string, unknown>) =>
    (await http.post<{ formula: CustomerFormula }>(`/customers/${id}/formulas`, body)).data.formula,
  /** 边写边检查：和保存时一样的检查，不保存。返回问题列表，空 = 通过 */
  checkFormula: async (id: string, body: Record<string, unknown>) =>
    (await http.post<{ problems: string[] }>(`/customers/${id}/formulas/check`, body)).data.problems,
  /** 试运行：失败（公式有问题 / 参数不对 / SQL 出错）也是 200，看 result.status */
  trialFormula: async (id: string, payload: { formula: Record<string, unknown>; params: Record<string, unknown>; inputs: FormulaTrialInput[] }) =>
    (await http.post<{ result: FormulaTrialResult }>(`/customers/${id}/formulas/trial`, payload, { timeout: 60_000 })).data.result,
  setFormulaStatus: async (id: string, formulaId: string, status: 'active' | 'deprecated') =>
    (await http.put<{ formula: CustomerFormula }>(`/customers/${id}/formulas/${formulaId}/status`, { status })).data.formula,
  removeFormula: async (id: string, formulaId: string) => http.delete(`/customers/${id}/formulas/${formulaId}`),
}

export const memoryApi = {
  update: async (id: number, payload: { value: string }) =>
    (await http.put<{ memory: MemoryItem }>(`/memories/${id}`, payload)).data.memory,
  remove: async (id: number) => http.delete(`/memories/${id}`),
}

// ---------------------------------------------------------------- 会话
export const conversationApi = {
  list: async (customerId?: string) =>
    (await http.get<{ conversations: ConversationBrief[] }>('/conversations', { params: { customer_id: customerId } })).data
      .conversations,
  create: async (payload: { customer_id?: string | null; recipe_id?: string | null; title?: string }) =>
    (await http.post<{ conversation: ConversationBrief }>('/conversations', payload)).data.conversation,
  get: async (id: string) => (await http.get<{ conversation: ConversationDetail & { running: boolean } }>(`/conversations/${id}`)).data.conversation,
  /** 脱敏情况：处理了哪些文件和列、发送检查与还原的次数、最近的处理记录 */
  privacy: async (id: string) => (await http.get<{ privacy: PrivacyOverview }>(`/conversations/${id}/privacy`)).data.privacy,
  privacySends: async (id: string) => (await http.get<{ sends: PrivacySend[] }>(`/conversations/${id}/privacy/sends`)).data.sends ?? [],
  privacyRestores: async (id: string) => (await http.get<{ restores: PrivacyRestore[] }>(`/conversations/${id}/privacy/restores`)).data.restores ?? [],
  privacyBlocks: async (id: string) => (await http.get<{ blocks: PrivacyBlock[] }>(`/conversations/${id}/privacy/blocks`)).data.blocks ?? [],
  update: async (id: string, payload: Partial<{ title: string; customer_id: string | null }>) =>
    (await http.put<{ conversation: ConversationBrief }>(`/conversations/${id}`, payload)).data.conversation,
  remove: async (id: string) => http.delete(`/conversations/${id}`),
  bind: async (id: string, payload: { customer_id?: string; new_customer?: { name: string; code?: string; description?: string } }) =>
    (await http.post<{ conversation: ConversationBrief; customer_id: string }>(`/conversations/${id}/bind`, payload)).data,
  upload: async (id: string, file: File, kind: 'input' | 'expected' = 'input') => {
    const form = new FormData()
    form.append('file', file)
    form.append('kind', kind)
    return (await http.post<{ file: FileInfo; tables: TableBrief[] }>(`/conversations/${id}/files`, form)).data
  },
  loadFile: async (id: string, fileId: string) =>
    (await http.post<{ tables: TableBrief[] }>(`/conversations/${id}/load-file`, { file_id: fileId })).data.tables,
  tablePage: async (id: string, name: string, offset = 0, limit = 50, sourceConversationId?: string) =>
    (await http.get<{ table: TablePage }>(`/conversations/${id}/tables/${encodeURIComponent(name)}`, { params: { offset, limit, source_conversation_id: sourceConversationId } }))
      .data.table,
  dropTable: async (id: string, name: string, sourceConversationId?: string) =>
    http.delete(`/conversations/${id}/tables/${encodeURIComponent(name)}`, { params: { source_conversation_id: sourceConversationId } }),
  setTableRole: async (id: string, name: string, role: 'input' | 'expected', sourceConversationId?: string) =>
    (await http.put<{ table: TableBrief }>(`/conversations/${id}/tables/${encodeURIComponent(name)}/role`, { role }, { params: { source_conversation_id: sourceConversationId } })).data.table,
  stop: async (id: string) => http.post(`/conversations/${id}/stop`),
  steer: async (id: string, message: string) =>
    (await http.post<{ message_id: number; status: 'queued'; job_id: string; execution_id?: string }>(`/conversations/${id}/steer`, { message })).data,
  withdrawSteer: async (id: string, messageId: number) => http.delete(`/conversations/${id}/steer/${messageId}`),
  resume: (id: string, onEvent: (e: AgentEvent) => void) => postStream(`/conversations/${id}/resume`, {}, onEvent),
  /** thinking=true 是思考模式：后端先规划、再逐步执行并核对，中途不需要用户确认计划 */
  chat: (id: string, body: { message: string; attachments?: string[]; thinking?: boolean; recipe_edit?: RecipeEditRequest }, onEvent: (e: AgentEvent) => void) =>
    postStream(`/conversations/${id}/chat`, body, onEvent),
  events: (id: string, jobId: string, onEvent: (e: AgentEvent) => void) =>
    getStream(`/conversations/${id}/events?job_id=${encodeURIComponent(jobId)}&snapshot=true`, onEvent),
}

// ---------------------------------------------------------------- 文件
export interface DataPreviewPage {
  filename: string
  sheet: string
  sheets: string[]
  total: number
  total_exact: boolean
  has_more: boolean
  offset: number
  limit: number
  column_offset: number
  raw: true
  columns: string[]
  rows: unknown[][]
  warnings: string[]
}

export const fileApi = {
  get: async (id: string) => (await http.get<{ file: FileInfo }>(`/files/${id}`)).data.file,
  /** 改文件名（只改名字，不重跑，扩展名不能变）：输入文件同步流程里记录的名字；输出文件必须带 recipeId，同步流程里声明的名字。 */
  rename: async (id: string, filename: string, scope: { customerId?: string; recipeId?: string } = {}) =>
    (await http.patch<{ old: string; filename: string; changed: boolean; recipes: { recipe_id: string; version: number; customer_id: string | null }[] }>(
      `/files/${id}`, { filename, customer_id: scope.customerId, recipe_id: scope.recipeId })).data,
  /** 在线查看用的内容：脱敏的列是令牌（下载才是真实值）。 */
  content: async (id: string, signal?: AbortSignal) =>
    (await http.get<ArrayBuffer>(`/files/${id}/content`, { responseType: 'arraybuffer', signal, params: { t: Date.now() } })).data,   // 脱敏状态随时会变，不能用浏览器里之前缓存的内容
  /** 文件列表里每个表格文件的脱敏状态；不是表格文件、或没有权限的文件不在返回里。 */
  privacyStatus: async (ids: string[]) =>
    (await http.post<{ status: Record<string, PrivacyFileStatus> }>('/files/privacy-status', { ids: ids.slice(0, 200) })).data.status ?? {},
  /** 脱敏维护页：每一列现在是否脱敏（没识别过的文件会先识别）。 */
  privacyColumns: async (id: string, conversationId?: string) =>
    (await http.get<{ columns: PrivacyColumns }>(`/files/${id}/privacy-columns`, { params: { conversation_id: conversationId } })).data.columns,
  /** 手动改某些列：脱敏 / 不脱敏 / 恢复自动（choice 为 null）。 */
  savePrivacyColumns: async (id: string, changes: PrivacyColumnChange[], conversationId?: string) =>
    (await http.put<{ columns: PrivacyColumns }>(`/files/${id}/privacy-columns`, { changes, conversation_id: conversationId })).data.columns,
  /** 脱敏副本预览：AI 实际看到的样子（被保护的格子是令牌）。conversationId 用来把查看记到该会话的处理记录里。 */
  privacyPreview: async (id: string, options: { sheet?: string; offset?: number; limit?: number; conversationId?: string } = {}) =>
    (await http.get<{ preview: PrivacyCopyPreview }>(`/files/${id}/privacy-preview`, {
      params: { sheet: options.sheet, offset: options.offset ?? 0, limit: options.limit ?? 50, conversation_id: options.conversationId },
    })).data.preview,
  preview: async (id: string, sheet?: string, offset = 0, limit = 50) =>
    (await http.get<{ preview: { filename: string; sheet: string; sheets: string[]; total: number; columns: string[]; rows: unknown[][] } }>(
      `/files/${id}/preview`, { params: { sheet, offset, limit } })).data.preview,
  previewUpload: async (file: File, sheet?: string, offset = 0, limit = 50, signal?: AbortSignal) => {
    const form = new FormData()
    form.append('file', file)
    return (await http.post<{ preview: { filename: string; sheet: string; sheets: string[]; total: number; columns: string[]; rows: unknown[][] } }>(
      '/files/preview-upload', form, { params: { sheet, offset, limit }, signal })).data.preview
  },
  previewData: async (id: string, sheet?: string, offset = 0, limit = 100, signal?: AbortSignal) =>
    (await http.get<{ preview: DataPreviewPage }>(`/files/${id}/preview`, {
      params: { sheet, offset, limit, raw: true }, signal,
    })).data.preview,
  previewDataUpload: async (file: File, sheet?: string, offset = 0, limit = 100, signal?: AbortSignal) => {
    const form = new FormData()
    form.append('file', file)
    return (await http.post<{ preview: DataPreviewPage }>('/files/preview-upload', form, {
      params: { sheet, offset, limit, raw: true }, signal,
    })).data.preview
  },
  remove: async (id: string, customerId?: string, recipeId?: string, conversationId?: string) =>
    http.delete(`/files/${id}`, { params: { customer_id: customerId, recipe_id: recipeId, conversation_id: conversationId } }),
  downloadUrl: (id: string) => `/api/files/${id}/download?t=${Date.now()}`,
}

// ---------------------------------------------------------------- 流程 / 运行
export const recipeApi = {
  list: async (params: { customer_id?: string; conversation_id?: string }) =>
    (await http.get<{ recipes: RecipeBrief[] }>('/recipes', { params })).data.recipes,
  get: async (id: string, version?: number) =>
    (await http.get<{ recipe: RecipeJSON; flow: RecipeFlowData; versions: { version: number; created_at: string; steps: number; note: string }[]; runs: RunBrief[] }>(
      `/recipes/${id}`, { params: { version } })).data,
  update: async (id: string, recipe: RecipeJSON) => (await http.put<{ recipe: RecipeBrief }>(`/recipes/${id}`, recipe)).data.recipe,
  remove: async (id: string) => http.delete(`/recipes/${id}`),
  match: async (id: string, fileIds: string[]) =>
    (await http.post<{ inputs: { alias: string; kind?: 'table' | 'file'; description: string; file_pattern: string; required_columns: string[]; match: { key: string; score: number; filename: string; sheet: string | null } | null }[]; candidates: { key: string; kind?: 'table' | 'file'; filename: string; sheet: string | null; rows: number }[] }>(
      `/recipes/${id}/match`, { file_ids: fileIds })).data,
  run: (id: string, body: { file_ids: string[]; inputs?: Record<string, string>; allow_repair?: boolean; version?: number; scoped?: boolean; label?: string }, onEvent: (e: AgentEvent) => void) =>
    postStream(`/recipes/${id}/run`, body, onEvent),
  /** 修改流程名称：原地改名，不产生新版本（所有版本里的名字一起改）；名称没变时 saved 为 false。 */
  rename: async (id: string, name: string) =>
    (await http.post<{ saved: boolean; version: number; name: string }>(`/recipes/${id}/nodes/edit`,
      { edits: [{ op: 'update_workflow', name }] })).data,
  formulas: async (customerId?: string) =>
    (await http.get<{ builtin: FormulaInfo[]; customer: FormulaInfo[]; reserved_ids: string[] }>('/formulas', { params: { customer_id: customerId } })).data,
  /** 新增 / 升级内置公式（所有客户共用） */
  saveGlobalFormula: async (body: Record<string, unknown>) => (await http.post<{ formula: FormulaInfo }>('/formulas', body)).data.formula,
  /** 内置公式不绑定客户：参考表直接上传，生成和检查都不带客户 */
  uploadGlobalFormulaFile: async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return (await http.post<{ file: FileInfo }>('/formulas/files', form)).data.file
  },
  generateGlobalFormula: async (payload: { description: string; file_ids?: string[]; tables?: { file_id: string; sheet?: string }[]; formula?: Record<string, unknown> }) =>
    (await http.post<FormulaGenerationResult>('/formulas/generate', payload, { timeout: 0 })).data,
  checkGlobalFormula: async (body: Record<string, unknown>) => (await http.post<{ problems: string[] }>('/formulas/check', body)).data.problems,
  formulaSuggestions: async () => (await http.get<{ suggestions: FormulaSuggestion[] }>('/formulas/suggestions')).data.suggestions,
  dismissFormulaSuggestion: async (id: string) => http.post(`/formulas/suggestions/${id}/dismiss`),
  /** 内置公式试运行：按 id 取公式，表是直接上传的文件 */
  trialGlobalFormula: async (payload: { formula_id: string; params: Record<string, unknown>; inputs: FormulaTrialInput[] }) =>
    (await http.post<{ result: FormulaTrialResult }>('/formulas/trial', payload, { timeout: 60_000 })).data.result,
  /** 删除内置公式：自己添加的彻底删除，自带的不再可用 */
  removeGlobalFormula: async (id: string) => http.delete(`/formulas/${id}`),
  /** 哪些客户的流程用到了这个公式 */
  formulaReferences: async (id: string) =>
    (await http.get<{ references: { recipe_id: string; name: string; customer_name: string }[] }>(`/formulas/${id}/references`)).data.references,
}

export const runApi = {
  list: async (params: { customer_id?: string; recipe_id?: string }) =>
    (await http.get<{ runs: RunBrief[] }>('/runs', { params })).data.runs,
  get: async (id: string) =>
    (await http.get<{ run: RunDetail; tables: { name: string; label: string; rows: number; cols: number; source: string }[] }>(`/runs/${id}`)).data,
  tablePage: async (id: string, name: string, offset = 0, limit = 50) =>
    (await http.get<{ table: TablePage }>(`/runs/${id}/tables/${encodeURIComponent(name)}`, { params: { offset, limit } })).data.table,
  rerun: (id: string, stepId: number, params: Record<string, unknown> | null, onEvent: (e: AgentEvent) => void) =>
    postStream(`/runs/${id}/rerun/${stepId}`, { params }, onEvent),
}

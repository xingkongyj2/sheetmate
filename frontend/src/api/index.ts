/**
 * 后端接口封装（按资源分组）。所有函数返回解析好的数据，出错抛 ApiError。
 */
import { http } from './http'
import { postStream } from './sse'
import type {
  AgentEvent,
  ConversationBrief,
  ConversationDetail,
  CustomerCard,
  CustomerDetail,
  FileInfo,
  FormulaInfo,
  LLMConfig,
  MemoryItem,
  PlanStep,
  RecipeBrief,
  RecipeFlowData,
  RecipeJSON,
  RunBrief,
  RunDetail,
  TableBrief,
  TablePage,
  User,
} from './types'

// ---------------------------------------------------------------- 认证
export const authApi = {
  me: async () => (await http.get<{ user: User }>('/auth/me')).data.user,
  login: async (account: string, password: string) =>
    (await http.post<{ user: User }>('/auth/login', { account, password })).data.user,
  register: async (account: string, password: string, display_name = '') =>
    (await http.post<{ user: User }>('/auth/register', { account, password, display_name })).data.user,
  logout: async () => http.post('/auth/logout'),
  updateProfile: async (payload: { display_name?: string; profile?: Record<string, unknown> }) =>
    (await http.put<{ user: User }>('/auth/profile', payload)).data.user,
}

// ---------------------------------------------------------------- 设置
export const settingsApi = {
  getLLM: async () => (await http.get<LLMConfig>('/settings/llm')).data,
  saveLLM: async (payload: { api_key?: string; base_url?: string; model?: string }) =>
    (await http.put<LLMConfig>('/settings/llm', payload)).data,
  testLLM: async (payload: { api_key?: string; base_url?: string; model?: string }) =>
    (await http.post<{ ok: boolean; reply?: string; model?: string; error?: { message: string } }>('/settings/llm/test', payload,
      { validateStatus: () => true })).data,
}

// ---------------------------------------------------------------- 客户
export const customerApi = {
  list: async () => (await http.get<{ customers: CustomerCard[] }>('/customers')).data.customers,
  get: async (id: string) => (await http.get<{ customer: CustomerDetail }>(`/customers/${id}`)).data.customer,
  create: async (payload: { name: string; code?: string; description?: string }) =>
    (await http.post<{ customer: CustomerCard }>('/customers', payload)).data.customer,
  update: async (id: string, payload: Partial<{ name: string; code: string; description: string; summary: string; mascot: string }>) =>
    (await http.put<{ customer: CustomerCard }>(`/customers/${id}`, payload)).data.customer,
  remove: async (id: string) => http.delete(`/customers/${id}`),
  refreshSummary: async (id: string) =>
    (await http.post<{ summary: string; by_ai: boolean }>(`/customers/${id}/summary`, {}, { timeout: 180_000 })).data,
  uploadFile: async (id: string, file: File, kind = 'input') => {
    const form = new FormData()
    form.append('file', file)
    form.append('kind', kind)
    return (await http.post<{ file: FileInfo }>(`/customers/${id}/files`, form)).data.file
  },
  saveMapping: async (id: string, mapping: Record<string, string[]>) =>
    (await http.put<{ mapping: Record<string, string[]> }>(`/customers/${id}/field-mapping`, { mapping })).data.mapping,
  addMemory: async (id: string, payload: { category: string; key: string; value: string }) =>
    (await http.post<{ memory: MemoryItem }>(`/customers/${id}/memories`, payload)).data.memory,
}

export const memoryApi = {
  remove: async (id: number) => http.delete(`/memories/${id}`),
}

// ---------------------------------------------------------------- 会话
export const conversationApi = {
  list: async (customerId?: string) =>
    (await http.get<{ conversations: ConversationBrief[] }>('/conversations', { params: { customer_id: customerId } })).data
      .conversations,
  create: async (payload: { customer_id?: string | null; mode?: string; title?: string }) =>
    (await http.post<{ conversation: ConversationBrief }>('/conversations', payload)).data.conversation,
  get: async (id: string) => (await http.get<{ conversation: ConversationDetail & { running: boolean } }>(`/conversations/${id}`)).data.conversation,
  update: async (id: string, payload: Partial<{ title: string; mode: string; customer_id: string | null }>) =>
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
  tablePage: async (id: string, name: string, offset = 0, limit = 50) =>
    (await http.get<{ table: TablePage }>(`/conversations/${id}/tables/${encodeURIComponent(name)}`, { params: { offset, limit } }))
      .data.table,
  dropTable: async (id: string, name: string) => http.delete(`/conversations/${id}/tables/${encodeURIComponent(name)}`),
  setTableRole: async (id: string, name: string, role: 'input' | 'expected') =>
    (await http.put<{ table: TableBrief }>(`/conversations/${id}/tables/${encodeURIComponent(name)}/role`, { role })).data.table,
  plan: async (id: string, message: string) =>
    (await http.post<{ plan: { title: string; steps: PlanStep[] } }>(`/conversations/${id}/plan`, { message })).data.plan,
  stop: async (id: string) => http.post(`/conversations/${id}/stop`),
  chat: (id: string, body: { message: string; mode?: string; plan_steps?: PlanStep[]; attachments?: string[] }, onEvent: (e: AgentEvent) => void) =>
    postStream(`/conversations/${id}/chat`, body, onEvent),
}

// ---------------------------------------------------------------- 文件
export const fileApi = {
  preview: async (id: string, sheet?: string, offset = 0, limit = 50) =>
    (await http.get<{ preview: { filename: string; sheet: string; sheets: string[]; total: number; columns: string[]; rows: unknown[][] } }>(
      `/files/${id}/preview`, { params: { sheet, offset, limit } })).data.preview,
  remove: async (id: string) => http.delete(`/files/${id}`),
  downloadUrl: (id: string) => `/api/files/${id}/download`,
}

// ---------------------------------------------------------------- 流程 / 运行
export const recipeApi = {
  list: async (params: { customer_id?: string; conversation_id?: string }) =>
    (await http.get<{ recipes: RecipeBrief[] }>('/recipes', { params })).data.recipes,
  get: async (id: string, version?: number) =>
    (await http.get<{ recipe: RecipeJSON; flow: RecipeFlowData; versions: { version: number; status: string; status_label: string; created_at: string; steps: number; note: string }[]; runs: RunBrief[] }>(
      `/recipes/${id}`, { params: { version } })).data,
  update: async (id: string, recipe: RecipeJSON) => (await http.put<{ recipe: RecipeBrief }>(`/recipes/${id}`, recipe)).data.recipe,
  setStatus: async (id: string, status: string) =>
    (await http.post<{ recipe: RecipeBrief }>(`/recipes/${id}/status`, { status })).data.recipe,
  remove: async (id: string) => http.delete(`/recipes/${id}`),
  match: async (id: string, fileIds: string[]) =>
    (await http.post<{ inputs: { alias: string; description: string; file_pattern: string; required_columns: string[]; match: { key: string; score: number; filename: string; sheet: string } | null }[]; candidates: { key: string; filename: string; sheet: string; rows: number }[] }>(
      `/recipes/${id}/match`, { file_ids: fileIds })).data,
  run: (id: string, body: { file_ids: string[]; inputs?: Record<string, string>; allow_repair?: boolean }, onEvent: (e: AgentEvent) => void) =>
    postStream(`/recipes/${id}/run`, body, onEvent),
  formulas: async (customerId?: string) =>
    (await http.get<{ builtin: FormulaInfo[]; customer: FormulaInfo[] }>('/formulas', { params: { customer_id: customerId } })).data,
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

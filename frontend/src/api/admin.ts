/** 管理后台接口封装（仅管理员角色可访问）。 */
import { http } from './http'
import type { ExecutionMetrics } from './observability'

export interface AdminUserCustomerRef {
  id: string
  name: string
  mascot: string
}

export interface AdminUserRow {
  id: number
  account: string
  display_name: string
  avatar: string
  role: 'admin' | 'member'
  role_label: string
  created_at: string
  last_active_at: string
  customer_ids: string[]
  customers: AdminUserCustomerRef[]
  owned_customer_count: number
  conversation_count: number
  task_count: number
}

export interface AdminCustomerMemberRef {
  id: number
  account: string
  display_name: string
  avatar: string
  role: 'admin' | 'member'
}

export interface AdminCustomerRow {
  id: string
  name: string
  code: string
  description: string
  mascot: string
  creator: { id: number; account: string; display_name: string } | null
  member_ids: number[]
  members: AdminCustomerMemberRef[]
  recipe_count: number
  formula_count: number
  conversation_count: number
  run_count: number
  file_count: number
  storage_bytes: number
  created_at: string
  last_activity: string
}

export interface AdminOverview {
  counts: {
    users: number
    admins: number
    members: number
    customers: number
    recipes: number
    conversations: number
    files: number
    storage_bytes: number
    formulas_custom: number
    formulas_builtin: number
    runs_total: number
    runs_success: number
  }
  system: {
    llm: {
      configured: boolean
      provider_id: string | null
      provider_name: string
      model: string
      thinking_enabled: boolean
    }
    privacy: {
      enabled: boolean
      enabled_rules: number
      total_rules: number
      llm_configured: boolean
      llm_model: string
    }
  }
  recent_metrics: {
    days: number
    tasks: number
    success_rate: number | null
    latency: ExecutionMetrics['latency']
    usage: ExecutionMetrics['usage']
    daily: ExecutionMetrics['daily']
  }
  recent_users: AdminUserRow[]
  recent_customers: AdminCustomerRow[]
}

export interface AdminStatsUserRow {
  user_id: number
  account: string
  display_name: string
  avatar: string
  role: 'admin' | 'member'
  role_label: string
  tasks: number
  completed: number
  failed: number
  success_rate: number | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  model_calls: number
  conversations: number
}

export interface AdminStatsCustomerRow {
  customer_id: string
  name: string
  mascot: string
  tasks: number
  completed: number
  failed: number
  total_tokens: number
  conversations: number
  recipes: number
}

export interface AdminExecutionMetrics extends Omit<ExecutionMetrics, 'scope'> {
  scope: 'all' | 'user'
  user_id: number | null
  by_user: AdminStatsUserRow[]
  by_customer: AdminStatsCustomerRow[]
  users: { id: number; account: string; display_name: string; role: 'admin' | 'member' }[]
}

export const adminApi = {
  overview: async () => (await http.get<AdminOverview>('/admin/overview')).data,
  stats: async (days: number, userId?: number | null) =>
    (await http.get<AdminExecutionMetrics>('/admin/stats', {
      params: { days, ...(userId ? { user_id: userId } : {}) },
    })).data,
  listUsers: async () => (await http.get<{ users: AdminUserRow[] }>('/admin/users')).data.users,
  createUser: async (payload: {
    account: string
    password: string
    display_name?: string
    role?: 'admin' | 'member'
    customer_ids?: string[]
  }) => (await http.post<{ user: AdminUserRow }>('/admin/users', payload)).data.user,
  updateUser: async (
    userId: number,
    payload: {
      display_name?: string
      role?: 'admin' | 'member'
      password?: string
      customer_ids?: string[]
    },
  ) => (await http.put<{ user: AdminUserRow }>(`/admin/users/${userId}`, payload)).data.user,
  deleteUser: async (userId: number) => http.delete(`/admin/users/${userId}`),
  listCustomers: async () => (await http.get<{ customers: AdminCustomerRow[] }>('/admin/customers')).data.customers,
  setCustomerMembers: async (customerId: string, userIds: number[]) =>
    (await http.put<{ customer: AdminCustomerRow }>(`/admin/customers/${customerId}/members`, { user_ids: userIds })).data.customer,
}

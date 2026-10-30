import { onScopeDispose, readonly, ref, shallowRef } from 'vue'

import { errorText } from '@/api/http'

export interface ConfirmOptions {
  title: string
  description: string
  target?: string
  tone?: 'danger' | 'warning'
  confirmText?: string
  cancelText?: string
  pendingText?: string
  action?: () => unknown | Promise<unknown>
}

interface ConfirmRequest extends ConfirmOptions {
  owner: symbol
}

const request = shallowRef<ConfirmRequest | null>(null)
const busy = ref(false)
const error = ref('')
let resolveRequest: ((accepted: boolean) => void) | null = null

export const confirmation = readonly(request)
export const confirmationBusy = readonly(busy)
export const confirmationError = readonly(error)

function finish(accepted: boolean) {
  const resolve = resolveRequest
  resolveRequest = null
  request.value = null
  busy.value = false
  error.value = ''
  resolve?.(accepted)
}

export function cancelConfirmation() {
  if (!busy.value) finish(false)
}

export function resetConfirmation() {
  finish(false)
}

export async function acceptConfirmation() {
  const current = request.value
  if (!current || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await current.action?.()
    if (request.value === current) finish(true)
  } catch (reason) {
    if (request.value === current) error.value = errorText(reason) || '操作失败，请重试'
  } finally {
    if (request.value === current) busy.value = false
  }
}

export function useConfirm() {
  const owner = Symbol('confirmation')
  onScopeDispose(() => {
    if (request.value?.owner === owner) finish(false)
  })

  function confirm(options: ConfirmOptions): Promise<boolean> {
    if (request.value) return Promise.resolve(false)
    return new Promise((resolve) => {
      resolveRequest = resolve
      request.value = { tone: 'warning', confirmText: '确认', cancelText: '取消', pendingText: '正在处理…', ...options, owner }
    })
  }

  function confirmDelete(options: Omit<ConfirmOptions, 'tone'>) {
    return confirm({ confirmText: '确认删除', pendingText: '正在删除…', ...options, tone: 'danger' })
  }

  return { confirm, confirmDelete }
}

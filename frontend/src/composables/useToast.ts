/** 轻提示（右下角），对应 daisyUI toast */
import { reactive } from 'vue'

export interface Toast {
  id: number
  kind: 'info' | 'success' | 'error'
  text: string
}

export const toasts = reactive<Toast[]>([])
let seq = 0

function push(kind: Toast['kind'], text: string, ms = 3200) {
  const id = ++seq
  toasts.push({ id, kind, text })
  setTimeout(() => {
    const index = toasts.findIndex((t) => t.id === id)
    if (index >= 0) toasts.splice(index, 1)
  }, ms)
}

export function useToast() {
  return {
    info: (text: string) => push('info', text),
    success: (text: string) => push('success', text),
    error: (text: string) => push('error', text, 5000),
  }
}

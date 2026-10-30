/**
 * 全局鼠标位置（所有 IP 形象共用一个监听，眼睛跟着鼠标转）。
 */
import { reactive } from 'vue'

export const pointer = reactive({ x: -1, y: -1, active: false })

let installed = false
let frame = 0

export function usePointer() {
  if (!installed && typeof window !== 'undefined') {
    installed = true
    window.addEventListener(
      'pointermove',
      (e) => {
        cancelAnimationFrame(frame)
        frame = requestAnimationFrame(() => {
          pointer.x = e.clientX
          pointer.y = e.clientY
          pointer.active = true
        })
      },
      { passive: true },
    )
    window.addEventListener('pointerleave', () => (pointer.active = false))
  }
  return pointer
}

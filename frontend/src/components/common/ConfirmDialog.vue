<script setup lang="ts">
import { CircleAlert, LoaderCircle, Trash2, TriangleAlert, X } from '@lucide/vue'
import { nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'

import {
  acceptConfirmation, cancelConfirmation, confirmation, confirmationBusy, confirmationError, resetConfirmation,
} from '@/composables/useConfirm'

const dialog = ref<HTMLDialogElement | null>(null)
const cancelButton = ref<HTMLButtonElement | null>(null)
const id = useId()
let previousOverflow: string | null = null
let backdropPointerDown = false

function unlockScroll() {
  if (previousOverflow === null) return
  document.body.style.overflow = previousOverflow
  previousOverflow = null
}

function onKeydown(event: KeyboardEvent) {
  if (!dialog.value?.open || !['Escape', 'Tab'].includes(event.key)) return
  event.preventDefault()
  event.stopImmediatePropagation()
  if (event.key === 'Escape') {
    cancelConfirmation()
    return
  }
  const buttons = Array.from(dialog.value.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'))
  if (!buttons.length) {
    dialog.value.focus({ preventScroll: true })
    return
  }
  const activeIndex = buttons.findIndex(button => button === document.activeElement)
  const nextIndex = event.shiftKey
    ? (activeIndex <= 0 ? buttons.length - 1 : activeIndex - 1)
    : (activeIndex + 1) % buttons.length
  buttons[nextIndex]?.focus({ preventScroll: true })
}

watch(confirmation, async (current) => {
  await nextTick()
  if (confirmation.value !== current) return
  if (current) {
    previousOverflow ??= document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialog.value?.showModal()
    window.addEventListener('keydown', onKeydown, true)
    cancelButton.value?.focus({ preventScroll: true })
  } else {
    window.removeEventListener('keydown', onKeydown, true)
    dialog.value?.close()
    backdropPointerDown = false
    unlockScroll()
  }
}, { flush: 'post', immediate: true })

watch(confirmationBusy, (busy) => {
  if (!dialog.value?.open || !confirmation.value) return
  if (busy) dialog.value.focus({ preventScroll: true })
  else cancelButton.value?.focus({ preventScroll: true })
}, { flush: 'post' })

function outside(event: PointerEvent | MouseEvent) {
  if (event.target !== dialog.value || !dialog.value) return false
  const bounds = dialog.value.getBoundingClientRect()
  return event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom
}

function onBackdropClick(event: MouseEvent) {
  if (backdropPointerDown && outside(event)) cancelConfirmation()
  backdropPointerDown = false
}

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown, true)
  dialog.value?.close()
  unlockScroll()
  resetConfirmation()
})
</script>

<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      tabindex="-1"
      role="alertdialog"
      aria-modal="true"
      :aria-labelledby="`${id}-title`"
      :aria-describedby="confirmation?.target ? `${id}-target ${id}-description` : `${id}-description`"
      :aria-busy="confirmationBusy"
      class="fixed inset-0 m-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-[440px] overflow-y-auto rounded-3xl border border-line bg-white p-0 text-ink shadow-[0_24px_80px_-20px_rgba(0,0,0,0.3)] backdrop:bg-black/25 backdrop:backdrop-blur-[2px]"
      @cancel.prevent="cancelConfirmation"
      @pointerdown="backdropPointerDown = outside($event)"
      @click="onBackdropClick"
    >
      <div v-if="confirmation">
        <div class="px-6 pb-5 pt-6">
          <div class="flex items-start justify-between gap-4">
            <span class="flex size-11 items-center justify-center rounded-2xl" :class="confirmation.tone === 'danger' ? 'bg-error/10 text-error' : 'bg-fun-yellow/15 text-[#a56d0d]'">
              <Trash2 v-if="confirmation.tone === 'danger'" class="size-5" aria-hidden="true" />
              <TriangleAlert v-else class="size-5" aria-hidden="true" />
            </span>
            <button type="button" class="icon-btn -mr-1 -mt-1 disabled:cursor-not-allowed disabled:opacity-40" aria-label="关闭确认窗口" :disabled="confirmationBusy" @click="cancelConfirmation"><X class="size-4" /></button>
          </div>
          <h2 :id="`${id}-title`" class="mt-4 break-words text-lg font-semibold leading-7">{{ confirmation.title }}</h2>
          <p v-if="confirmation.target" :id="`${id}-target`" class="mt-3 max-h-32 overflow-y-auto rounded-xl border border-line bg-paper/70 px-3.5 py-3 text-[13px] font-medium leading-6 [overflow-wrap:anywhere]">{{ confirmation.target }}</p>
          <p :id="`${id}-description`" class="mt-3 whitespace-pre-line text-[13px] leading-6 text-mute [overflow-wrap:anywhere]">{{ confirmation.description }}</p>
          <div v-if="confirmationError" role="alert" class="mt-4 flex items-start gap-2 rounded-xl bg-error/5 px-3 py-2.5 text-[13px] leading-5 text-error">
            <CircleAlert class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span class="min-w-0 [overflow-wrap:anywhere]">{{ confirmationError }}</span>
          </div>
        </div>
        <div class="flex justify-end gap-2 border-t border-line bg-paper/40 px-6 py-4">
          <button ref="cancelButton" type="button" autofocus class="btn-line min-w-20 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink" :disabled="confirmationBusy" @click="cancelConfirmation">{{ confirmation.cancelText }}</button>
          <button
            type="button"
            class="inline-flex min-w-24 items-center justify-center gap-2 rounded-full px-5 py-2.5 text-sm font-medium text-white transition focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
            :class="confirmation.tone === 'danger' ? 'bg-error enabled:hover:bg-error/90 focus-visible:outline-error' : 'bg-ink enabled:hover:bg-black/85 focus-visible:outline-ink'"
            :disabled="confirmationBusy"
            @click="acceptConfirmation"
          >
            <LoaderCircle v-if="confirmationBusy" class="size-4 animate-spin" aria-hidden="true" />
            {{ confirmationBusy ? confirmation.pendingText : confirmation.confirmText }}
          </button>
        </div>
      </div>
    </dialog>
  </Teleport>
</template>

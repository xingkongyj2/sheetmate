<script setup lang="ts">
/** 弹窗（daisyUI modal 结构），点遮罩或按 Esc 关闭 */
import { X } from '@lucide/vue'
import { onBeforeUnmount, onMounted } from 'vue'

withDefaults(defineProps<{ title?: string; width?: string }>(), { title: '', width: 'max-w-3xl' })
const emit = defineEmits<{ close: [] }>()

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div class="modal modal-open" role="dialog">
      <div class="modal-box w-full animate-pop rounded-3xl border border-line p-0 shadow-2xl" :class="width">
        <div class="flex items-center justify-between border-b border-line px-6 py-4">
          <div class="min-w-0">
            <slot name="title"><h3 class="truncate text-base font-semibold">{{ title }}</h3></slot>
          </div>
          <button class="icon-btn" aria-label="关闭" @click="emit('close')"><X class="size-4" /></button>
        </div>
        <div class="max-h-[75vh] overflow-y-auto px-6 py-5">
          <slot />
        </div>
        <div v-if="$slots.footer" class="flex justify-end gap-2 border-t border-line px-6 py-4">
          <slot name="footer" />
        </div>
      </div>
      <div class="modal-backdrop bg-black/25 backdrop-blur-[2px]" @click="emit('close')" />
    </div>
  </Teleport>
</template>

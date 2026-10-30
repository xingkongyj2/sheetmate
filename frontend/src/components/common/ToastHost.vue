<script setup lang="ts">
import { CircleAlert, CircleCheck, Info } from '@lucide/vue'

import { toasts } from '@/composables/useToast'
</script>

<template>
  <div class="toast toast-end toast-bottom z-[100]">
    <TransitionGroup name="fade">
      <div
        v-for="t in toasts"
        :key="t.id"
        class="flex max-w-sm animate-pop items-start gap-2.5 rounded-2xl border border-line bg-white px-4 py-3 text-sm shadow-[0_12px_32px_-12px_rgba(0,0,0,0.25)]"
      >
        <CircleCheck v-if="t.kind === 'success'" class="mt-0.5 size-4 shrink-0 text-success" />
        <CircleAlert v-else-if="t.kind === 'error'" class="mt-0.5 size-4 shrink-0 text-error" />
        <Info v-else class="mt-0.5 size-4 shrink-0 text-ink" />
        <span class="leading-relaxed">{{ t.text }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

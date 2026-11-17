<script setup lang="ts">
/** 发送明细：每次发给 AI 前，发送检查补充换掉了哪些值。原文是在本机用令牌库还原出来的，只给当前用户看。 */
import { ArrowRight, LoaderCircle, RotateCw } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { conversationApi } from '@/api'
import { errorText } from '@/api/http'
import type { PrivacySend } from '@/api/types'
import BaseModal from '@/components/common/BaseModal.vue'

const props = defineProps<{ conversationId: string }>()
const emit = defineEmits<{ close: [] }>()

const sends = ref<PrivacySend[] | null>(null)
const loading = ref(false)
const error = ref('')
// 只列有替换的发送；和上一次完全一样的折成一行，不重复列
const shown = computed(() => (sends.value ?? []).filter((send) => send.items.length))
const total = computed(() => sends.value?.length ?? 0)

async function load() {
  loading.value = true
  error.value = ''
  try {
    sends.value = await conversationApi.privacySends(props.conversationId)
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}
onMounted(load)

function clock(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString('zh-CN', { hour12: false })
}
</script>

<template>
  <BaseModal width="max-w-2xl" @close="emit('close')">
    <template #title>
      <div class="min-w-0">
        <h3 class="text-base font-semibold">发送明细</h3>
        <p class="mt-0.5 text-xs leading-5 text-faint">发给 AI 前补充替换的内容，原文只在本机显示</p>
      </div>
    </template>

    <div v-if="!sends && loading" class="flex justify-center py-12" role="status" aria-label="加载中">
      <LoaderCircle class="size-5 animate-spin text-mute motion-reduce:animate-none" />
    </div>

    <div v-else-if="error" class="flex flex-col items-center gap-3 py-10 text-center">
      <p class="max-w-md break-words text-sm leading-6 text-error" role="alert">{{ error }}</p>
      <button type="button" class="btn-line !px-3 !py-1.5 text-xs" @click="load"><RotateCw class="size-3.5" />重试</button>
    </div>

    <p v-else-if="!shown.length" class="py-10 text-center text-sm text-faint">{{ total ? `${total} 次发送都无需替换` : '还没有发给 AI 的记录' }}</p>

    <ol v-else class="space-y-3" aria-label="每次发送">
      <li v-for="send in shown" :key="send.number" class="overflow-hidden rounded-xl border border-line">
        <div class="flex items-center gap-2 bg-paper/70 px-3.5 py-2 text-xs">
          <span class="font-medium text-ink">第 {{ send.number }} 次</span>
          <time class="font-mono tabular-nums text-faint">{{ clock(send.time) }}</time>
          <span class="ml-auto text-mute">替换 {{ send.items.length }} 个</span>
        </div>
        <p v-if="send.same_as_previous" class="px-3.5 py-2 text-xs text-faint">和上一次相同（对话记录随每次发送重复带上）</p>
        <table v-else class="w-full text-[13px] leading-5">
          <tbody>
            <tr v-for="item in send.items" :key="item.token" class="border-t border-line/70 first:border-t-0">
              <td class="w-full max-w-0 px-3.5 py-2">
                <div class="flex min-w-0 items-center gap-2">
                  <span class="min-w-0 break-all text-ink">{{ item.value ?? '（令牌库里没有这条记录）' }}</span>
                  <ArrowRight class="size-3.5 shrink-0 text-faint" aria-hidden="true" />
                  <code class="shrink-0 font-mono text-[11px] text-mute">{{ item.token }}</code>
                </div>
              </td>
              <td class="whitespace-nowrap px-3.5 py-2 text-xs text-mute">{{ item.kind_label }}</td>
              <td class="whitespace-nowrap px-3.5 py-2 text-right text-xs text-faint">{{ item.method_label }}</td>
            </tr>
          </tbody>
        </table>
      </li>
    </ol>
  </BaseModal>
</template>

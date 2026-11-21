<script setup lang="ts">
/** 模型脱敏明细，两页（原文 → 令牌，原文在本机还原，模型只看到令牌）：
 *  脱敏——文件脱敏之后仍以原文出现、发给模型前才换成令牌的值（每个值只在第一次出现的那次发送里列一次）；
 *  拦截——停止发送的原因，以及没能处理掉的值。 */
import { RotateCw } from '@lucide/vue'
import { onMounted, ref, watch } from 'vue'

import { conversationApi } from '@/api'
import { errorText } from '@/api/http'
import type { PrivacyBlock, PrivacySend } from '@/api/types'
import MascotLoader from '@/components/brand/MascotLoader.vue'
import BaseModal from '@/components/common/BaseModal.vue'

type Tab = 'sends' | 'blocks'
const props = withDefaults(defineProps<{ conversationId: string; tab?: Tab }>(), { tab: 'sends' })
const emit = defineEmits<{ close: [] }>()

const TABS = [{ id: 'sends', label: '脱敏' }, { id: 'blocks', label: '拦截' }] as const
const active = ref<Tab>(props.tab)
const sends = ref<PrivacySend[] | null>(null)
const blocks = ref<PrivacyBlock[] | null>(null)
const loaded = () => (active.value === 'sends' ? sends.value : blocks.value) !== null
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    if (active.value === 'sends') sends.value = await conversationApi.privacySends(props.conversationId)
    else blocks.value = await conversationApi.privacyBlocks(props.conversationId)
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}
onMounted(load)
watch(active, () => { if (!loaded()) void load() })

function clock(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString('zh-CN', { hour12: false })
}
</script>

<template>
  <BaseModal width="max-w-2xl" @close="emit('close')">
    <template #title>
      <div class="flex min-w-0 items-center gap-3">
        <h3 class="text-base font-semibold">模型脱敏明细</h3>
        <div class="flex gap-1" role="tablist" aria-label="明细类别">
          <button v-for="item in TABS" :key="item.id" type="button" role="tab"
            class="rounded-full px-2.5 py-0.5 text-xs leading-5 transition"
            :class="active === item.id ? 'bg-ink text-white' : 'text-mute hover:bg-paper hover:text-ink'"
            :aria-selected="active === item.id" @click="active = item.id">{{ item.label }}</button>
        </div>
      </div>
    </template>

    <MascotLoader v-if="loading && !loaded()" size="sm" class="py-12" />

    <div v-else-if="error" class="flex flex-col items-center gap-3 py-10 text-center">
      <p class="max-w-md break-words text-sm leading-6 text-error" role="alert">{{ error }}</p>
      <button type="button" class="btn-line !px-3 !py-1.5 text-xs" @click="load"><RotateCw class="size-3.5" />重试</button>
    </div>

    <template v-else-if="active === 'sends'">
      <p v-if="!sends?.length" class="py-10 text-center text-sm text-faint">没有需要脱敏的内容</p>
      <ol v-else class="space-y-3" aria-label="每次发送">
        <li v-for="send in sends" :key="send.number" class="overflow-hidden rounded-xl border border-line">
          <div class="flex items-center gap-2 bg-paper/70 px-3.5 py-2 text-xs">
            <span class="font-medium text-ink">第 {{ send.number }} 次发送</span>
            <time class="font-mono tabular-nums text-faint">{{ clock(send.time) }}</time>
            <span class="ml-auto text-mute">脱敏 {{ send.items.length }} 个</span>
          </div>
          <table class="w-full text-[13px] leading-5">
            <tbody>
              <tr v-for="item in send.items" :key="item.token" class="border-t border-line/70 first:border-t-0">
                <td class="w-1/2 max-w-0 break-all px-3.5 py-2 text-ink">{{ item.value ?? '—' }}</td>
                <td class="w-1/2 max-w-0 px-3.5 py-2">
                  <code class="break-all font-mono text-[12px] text-mute">{{ item.token }}</code>
                </td>
                <td class="whitespace-nowrap px-3.5 py-2 text-right text-xs text-mute">{{ item.kind_label }}</td>
              </tr>
            </tbody>
          </table>
        </li>
      </ol>
    </template>

    <template v-else>
      <p v-if="!blocks?.length" class="py-10 text-center text-sm text-faint">没有被拦截的发送</p>
      <ol v-else class="space-y-3" aria-label="每次拦截">
        <li v-for="block in blocks" :key="block.number" class="overflow-hidden rounded-xl border border-line">
          <div class="flex items-center gap-2 bg-paper/70 px-3.5 py-2 text-xs">
            <span class="font-medium text-ink">第 {{ block.number }} 次拦截</span>
            <time class="font-mono tabular-nums text-faint">{{ clock(block.time) }}</time>
            <span v-if="block.items.length" class="ml-auto text-mute">{{ block.items.length }} 个值没能处理</span>
          </div>
          <p class="border-t border-line/70 px-3.5 py-2 text-[13px] leading-5 text-error">{{ block.reason }}</p>
          <table v-if="block.items.length" class="w-full text-[13px] leading-5">
            <tbody>
              <tr v-for="item in block.items" :key="item.token" class="border-t border-line/70">
                <td class="w-1/2 max-w-0 break-all px-3.5 py-2 text-ink">{{ item.value ?? '—' }}</td>
                <td class="w-1/2 max-w-0 break-all px-3.5 py-2"><code class="font-mono text-[12px] text-mute">{{ item.token }}</code></td>
                <td class="whitespace-nowrap px-3.5 py-2 text-right text-xs text-mute">{{ item.kind_label }}</td>
              </tr>
            </tbody>
          </table>
        </li>
      </ol>
    </template>
  </BaseModal>
</template>

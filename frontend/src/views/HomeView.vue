<script setup lang="ts">
/**
 * 新对话（首页）：一句标题、一个输入框，IP 形象从输入框右上角探出，输入框下面挂着一个窄一点的抽屉「选择客户」。
 * 选客户相当于 Qoder 里「选择目录」，可以不选。发送时：建会话 → 上传附件 → 跳到对话页继续。
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { conversationApi } from '@/api'
import { errorText } from '@/api/http'
import LogoMark from '@/components/brand/LogoMark.vue'
import Mascot from '@/components/brand/Mascot.vue'
import ChatComposer, { type Attachment } from '@/components/chat/ChatComposer.vue'
import CustomerPicker from '@/components/chat/CustomerPicker.vue'
import { uid } from '@/composables/chatModel'
import { useToast } from '@/composables/useToast'
import { useWorkspaceStore } from '@/stores/conversations'
import { pendingStarts } from '@/stores/pending'

const store = useWorkspaceStore()
const router = useRouter()
const route = useRoute()
const toast = useToast()

const text = ref('')
const plan = ref(false)
const customerId = ref<string | null>(typeof route.query.customer === 'string' ? route.query.customer : null)
const files = ref<{ key: string; file: File; kind: 'input' | 'expected' }[]>([])
const attachments = ref<Attachment[]>([])
const busy = ref(false)

const customer = computed(() => store.customers.find((c) => c.id === customerId.value))

function attach(list: File[], kind: 'input' | 'expected') {
  for (const file of list) {
    const key = uid('f')
    files.value.push({ key, file, kind })
    attachments.value.push({ key, name: file.name, kind, status: 'pending' })
  }
}

function remove(key: string) {
  files.value = files.value.filter((f) => f.key !== key)
  attachments.value = attachments.value.filter((a) => a.key !== key)
}

async function send() {
  const message = text.value.trim()
  if (!message || busy.value) return
  busy.value = true
  try {
    const conv = await conversationApi.create({ customer_id: customerId.value })
    const ids: string[] = []
    for (const item of files.value) {
      const att = attachments.value.find((a) => a.key === item.key)!
      att.status = 'uploading'
      try {
        const result = await conversationApi.upload(conv.id, item.file, item.kind)
        att.status = 'done'
        ids.push(result.file.file_id)
      } catch (err) {
        att.status = 'error'
        att.error = errorText(err)
        throw err
      }
    }
    pendingStarts.set(conv.id, { message, plan: plan.value, attachments: ids })
    store.upsertConversation(conv)
    router.push({ name: 'chat', params: { id: conv.id } })
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="flex h-full flex-col overflow-y-auto">
    <div class="mx-auto flex w-full max-w-[680px] flex-1 flex-col justify-center px-6 pb-[26vh] pt-10">
      <h1 class="animate-rise flex items-center justify-center gap-3 text-center text-[30px] font-semibold leading-tight tracking-tight">
        <LogoMark :size="32" class="shrink-0" aria-hidden="true" />
        <span class="min-w-0">你的专属表格Agent助手</span>
      </h1>

      <div class="mt-[112px]">
        <div class="relative z-10">
          <div class="pointer-events-none absolute bottom-full right-4 aspect-[33/20] w-[160px] max-w-[calc(100%-1rem)] overflow-hidden sm:right-8">
            <Mascot :variant="customer?.mascot ?? 'blob'" :size="160" :mood="busy ? 'working' : 'idle'" :shadow="false" class="h-auto w-full" />
          </div>
          <ChatComposer
            v-model="text"
            v-model:plan="plan"
            :attachments="attachments"
            :busy="busy"
            large
            autofocus
            appearance="card"
            placeholder="表格的一切，交给我"
            @attach="attach"
            @remove="remove"
            @send="send"
          />
        </div>

        <div class="animate-drawer relative mx-6 -mt-5 rounded-b-[22px] bg-white pt-5 shadow-[0_4px_24px_rgba(0,0,0,0.06),0_1px_4px_rgba(0,0,0,0.03)] transition focus-within:shadow-[0_6px_28px_rgba(0,0,0,0.09),0_2px_6px_rgba(0,0,0,0.04)]">
          <CustomerPicker v-model="customerId" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 新对话（首页）：一句标题、一个输入框，IP 形象蹲在输入框的右上角，输入框下面挂着一个窄一点的抽屉「选择客户」。
 * 选客户相当于 Qoder 里「选择目录」，可以不选。发送时：建会话 → 上传附件 → 跳到对话页继续。
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { conversationApi } from '@/api'
import { errorText } from '@/api/http'
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
const mode = ref<'analyze' | 'modify'>('analyze')
const plan = ref(false)
const customerId = ref<string | null>(typeof route.query.customer === 'string' ? route.query.customer : null)
const files = ref<{ key: string; file: File; kind: 'input' | 'expected' }[]>([])
const attachments = ref<Attachment[]>([])
const busy = ref(false)

const customer = computed(() => store.customers.find((c) => c.id === customerId.value))

// IP 形象蹲在输入框上沿：身体底部在 SVG 的 107/120 处，往下压 SIT_DEPTH 像素，看起来是坐在边上而不是飘着
const MASCOT_SIZE = 72
const SIT_DEPTH = 6
const mascotTop = computed(() => `${-(MASCOT_SIZE * (107 / 120) - SIT_DEPTH)}px`)

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
    const conv = await conversationApi.create({ customer_id: customerId.value, mode: mode.value })
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
      <h1 class="animate-rise text-center text-[30px] font-semibold leading-tight tracking-tight">你的专属表格 Agent 助手</h1>

      <div class="mt-[84px]">
        <!-- 输入框：IP 形象蹲在右上角（装饰，不挡点击） -->
        <div class="relative z-10">
          <Mascot
            :variant="customer?.mascot ?? 'blob'"
            :size="MASCOT_SIZE"
            :mood="busy ? 'working' : 'idle'"
            :shadow="false"
            class="pointer-events-none absolute right-11"
            :style="{ top: mascotTop }"
          />
          <ChatComposer
            v-model="text"
            v-model:mode="mode"
            v-model:plan="plan"
            :attachments="attachments"
            :busy="busy"
            large
            autofocus
            :placeholder="mode === 'modify' ? '描述要怎么整理表格，可以附上人工做好的结果' : '问点什么，或把表格拖进来'"
            @attach="attach"
            @remove="remove"
            @send="send"
          />
        </div>

        <!-- 抽屉：从输入框下面延伸出来，比输入框窄一点，边框和输入框连成一体 -->
        <div class="animate-drawer relative mx-6 -mt-5 rounded-b-[18px] border border-t-0 border-line bg-paper/70 pt-5">
          <CustomerPicker v-model="customerId" />
        </div>
      </div>
    </div>
  </div>
</template>

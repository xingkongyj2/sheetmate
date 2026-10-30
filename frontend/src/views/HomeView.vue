<script setup lang="ts">
/**
 * 新对话：先选客户（相当于 Qoder 里选目录），再描述需求、拖入表格。
 * 发送时：建会话 → 上传附件 → 跳到对话页继续。
 */
import { Play } from '@lucide/vue'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { conversationApi } from '@/api'
import { errorText } from '@/api/http'
import FloatingObjects from '@/components/brand/FloatingObjects.vue'
import FunObject, { type FunKind } from '@/components/brand/FunObject.vue'
import Mascot from '@/components/brand/Mascot.vue'
import ChatComposer, { type Attachment } from '@/components/chat/ChatComposer.vue'
import CustomerPicker from '@/components/chat/CustomerPicker.vue'
import { uid } from '@/composables/chatModel'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceStore } from '@/stores/conversations'
import { pendingStarts, SUGGESTIONS } from '@/stores/pending'

const auth = useAuthStore()
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

const greeting = computed(() => {
  const h = new Date().getHours()
  const name = auth.user?.display_name ?? ''
  if (h < 6) return `夜深了，${name}`
  if (h < 11) return `早上好，${name}`
  if (h < 14) return `中午好，${name}`
  if (h < 18) return `下午好，${name}`
  return `晚上好，${name}`
})

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

function useSuggestion(s: (typeof SUGGESTIONS)[number]) {
  text.value = s.text
  mode.value = s.mode
}

function useRecipe(name: string) {
  mode.value = 'modify'
  text.value = `用流程「${name}」处理我上传的新文件`
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
  <div class="relative flex h-full flex-col overflow-y-auto">
    <!-- 漂浮物件只放在两侧留白里：窄屏时隐藏，避免压住输入框 -->
    <FloatingObjects preset="hero" class="hidden opacity-80 xl:block" />

    <div class="relative mx-auto flex w-full max-w-[780px] flex-1 flex-col justify-center px-6 py-12">
      <div class="flex animate-rise items-end gap-4">
        <Mascot :variant="customer?.mascot ?? 'blob'" :size="84" :mood="busy ? 'working' : 'idle'" />
        <div class="pb-2">
          <p class="text-sm text-mute">{{ greeting }}</p>
          <h1 class="mt-1 text-[30px] font-semibold leading-tight tracking-tight">今天想让表格变成什么样？</h1>
        </div>
      </div>

      <div class="mt-8 flex flex-wrap items-center gap-3">
        <CustomerPicker v-model="customerId" />
        <span class="text-xs text-faint">
          {{ customer ? `按「${customer.name}」的记忆和流程来处理` : '不绑定客户时，数据只属于这次对话，之后可以保存到客户' }}
        </span>
      </div>

      <div class="mt-3">
        <ChatComposer
          v-model="text"
          v-model:mode="mode"
          v-model:plan="plan"
          :attachments="attachments"
          :busy="busy"
          large
          autofocus
          :placeholder="mode === 'modify' ? '描述要怎么整理表格，例如：按运单号把费用拆成列，算合计，匹配归属…（可以附上人工做好的结果）' : '问点什么，例如：每个店铺的运费合计是多少？'"
          @attach="attach"
          @remove="remove"
          @send="send"
        />
      </div>

      <!-- 客户已有的流程：一键复用 -->
      <div v-if="customer && customer.recipes.length" class="mt-5">
        <div class="label-mini mb-2">「{{ customer.name }}」的处理流程</div>
        <div class="flex flex-wrap gap-2">
          <button v-for="r in customer.recipes" :key="r.recipe_id" class="btn-line" @click="useRecipe(r.name)">
            <Play class="size-3.5" />用「{{ r.name }}」处理新文件
            <span class="text-xs text-faint">{{ r.steps }} 步</span>
          </button>
        </div>
      </div>

      <div class="mt-6 grid gap-2 sm:grid-cols-2">
        <button
          v-for="s in SUGGESTIONS"
          :key="s.text"
          class="flex items-center gap-3 rounded-2xl border border-line bg-white/80 px-3.5 py-3 text-left text-[13px] text-mute backdrop-blur transition hover:-translate-y-0.5 hover:border-ink/20 hover:text-ink"
          @click="useSuggestion(s)"
        >
          <FunObject :kind="s.icon as FunKind" :size="30" />
          <span class="leading-snug">{{ s.text }}</span>
          <span class="tag ml-auto shrink-0">{{ s.mode === 'modify' ? '修改' : '分析' }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

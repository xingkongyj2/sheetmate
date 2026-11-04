<script setup lang="ts">
/**
 * 新对话（首页）：一句标题、一个输入框，IP 形象从输入框右上角探出，输入框下面挂着一个窄一点的抽屉「选择客户」。
 * 选客户相当于 Qoder 里「选择目录」，可以不选。发送时：建会话 → 上传附件 → 跳到对话页继续。
 */
import { storeToRefs } from 'pinia'
import { computed, defineAsyncComponent, onBeforeUnmount, ref } from 'vue'
import { isNavigationFailure, useRoute, useRouter } from 'vue-router'

import { conversationApi } from '@/api'
import { errorText } from '@/api/http'
import LogoMark from '@/components/brand/LogoMark.vue'
import Mascot from '@/components/brand/Mascot.vue'
import ChatComposer from '@/components/chat/ChatComposer.vue'
import CustomerPicker from '@/components/chat/CustomerPicker.vue'
import { uid } from '@/composables/chatModel'
import { useLocalFileReferences } from '@/composables/localFileReferences'
import { serializeTableReferences, type FileReference, type TableReference } from '@/composables/tableReferences'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceStore } from '@/stores/conversations'
import { useHomeDraftStore } from '@/stores/homeDraft'
import { pendingStarts } from '@/stores/pending'

const WorkbookPreview = defineAsyncComponent(() => import('@/features/workbook-preview/WorkbookPreview.vue'))

const store = useWorkspaceStore()
const auth = useAuthStore()
const draft = useHomeDraftStore()
const router = useRouter()
const route = useRoute()
const toast = useToast()

const { text, plan, customerId, files, attachments, busy, ready, savingFiles, cacheError } = storeToRefs(draft)
const composer = ref<InstanceType<typeof ChatComposer> | null>(null)
const viewingFile = ref<File | null>(null)
let active = true
onBeforeUnmount(() => { active = false })

const customer = computed(() => store.customers.find((c) => c.id === customerId.value))
const fileReferences = computed<FileReference[]>(() => attachments.value.map((item) => ({ filename: item.name })))
const { tableReferences, referencesLoading, referenceErrors } = useLocalFileReferences(files)
const referencesError = computed(() => {
  const failures = referenceErrors.value
  if (!failures.length) return ''
  const first = failures[0]!
  return `部分文件的工作表名称未能读取：${first.filename}（${first.error}）${failures.length > 1 ? `，另有 ${failures.length - 1} 个文件` : ''}。请检查文件后重新添加。`
})

async function restoreDraft() {
  if (!auth.user) return
  await draft.hydrate(auth.user.id)
  if (draft.ready) draft.initialize(typeof route.query.customer === 'string' ? route.query.customer : null)
}
void restoreDraft()

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

function preview(key: string) {
  const item = files.value.find((file) => file.key === key)
  if (!item) return
  composer.value?.captureSelection()
  viewingFile.value = item.file
}

async function send() {
  const message = text.value.trim()
  if (!message || busy.value || !ready.value) return
  if (draft.hasMissingFiles) {
    toast.error('请先恢复附件，或移除后重新选择')
    return
  }
  const submittedText = text.value
  const submittedFiles = [...files.value]
  const submittedPlan = plan.value
  const submittedCustomer = customerId.value
  const generation = draft.generation
  function clearSubmitted() {
    // 上传期间仍可编辑草稿，只清除本次实际提交的内容。
    if (text.value === submittedText) text.value = ''
    const submittedKeys = new Set(submittedFiles.map((file) => file.key))
    files.value = files.value.filter((file) => !submittedKeys.has(file.key))
    attachments.value = attachments.value.filter((attachment) => !submittedKeys.has(attachment.key))
    if (plan.value === submittedPlan) plan.value = false
    if (customerId.value === submittedCustomer) customerId.value = null
    draft.resetInitializationIfEmpty()
  }
  busy.value = true
  try {
    await draft.flush()
    if (generation !== draft.generation) return
    const conv = await conversationApi.create({ customer_id: submittedCustomer })
    if (generation !== draft.generation) return
    const ids: string[] = []
    const references: TableReference[] = []
    for (const item of submittedFiles) {
      const att = attachments.value.find((a) => a.key === item.key)
      if (att) att.status = 'uploading'
      try {
        const result = await conversationApi.upload(conv.id, item.file, item.kind)
        if (generation !== draft.generation) return
        if (att) att.status = 'done'
        ids.push(result.file.file_id)
        for (const table of result.tables) {
          const sheet = table.sheet || result.file.sheets.find((sheet) => table.label === `${result.file.filename} · ${sheet.sheet}`)?.sheet
          if (sheet) references.push({ filename: item.file.name, sheet, tableName: table.name })
        }
        if (/\.csv$/i.test(item.file.name) && result.tables.length === 1) {
          references.push({ filename: item.file.name, sheet: item.file.name.replace(/\.csv$/i, '') || 'CSV', tableName: result.tables[0]!.name })
        }
      } catch (err) {
        if (att) {
          att.status = 'error'
          att.error = errorText(err)
        }
        throw err
      }
    }
    pendingStarts.set(conv.id, {
      message: serializeTableReferences(message, references, submittedFiles.map((item) => ({ filename: item.file.name }))),
      plan: submittedPlan,
      attachments: ids,
    })
    store.upsertConversation(conv)
    if (!active) {
      clearSubmitted()
      toast.info('新会话已准备好，可从侧边栏打开继续')
      return
    }
    const navigation = await router.push({ name: 'chat', params: { id: conv.id } })
    if (isNavigationFailure(navigation)) {
      pendingStarts.delete(conv.id)
      return
    }
    if (generation === draft.generation) clearSubmitted()
  } catch (err) {
    if (generation === draft.generation) toast.error(errorText(err))
  } finally {
    if (generation === draft.generation) busy.value = false
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
            v-if="ready"
            ref="composer"
            v-model="text"
            v-model:plan="plan"
            :attachments="attachments"
            :file-references="fileReferences"
            :table-references="tableReferences"
            :references-loading="referencesLoading"
            :references-error="referencesError"
            :busy="busy"
            :stoppable="false"
            large
            autofocus
            appearance="card"
            placeholder="表格的一切，交给我"
            @attach="attach"
            @remove="remove"
            @preview="preview"
            @send="send"
          />
          <div v-else class="flex min-h-[160px] items-center justify-center gap-3 rounded-[22px] bg-white px-6 py-8 text-sm text-slate-500 shadow-[0_4px_24px_rgba(0,0,0,0.06)]" role="status">
            <span>{{ cacheError || '正在恢复草稿…' }}</span>
            <button v-if="cacheError" type="button" class="shrink-0 text-slate-700 underline underline-offset-4" @click="restoreDraft">重试</button>
          </div>
        </div>

        <div v-if="ready" class="animate-drawer relative mx-6 -mt-5 rounded-b-[22px] bg-white pt-5 shadow-[0_4px_24px_rgba(0,0,0,0.06),0_1px_4px_rgba(0,0,0,0.03)] transition focus-within:shadow-[0_6px_28px_rgba(0,0,0,0.09),0_2px_6px_rgba(0,0,0,0.04)]">
          <CustomerPicker v-model="customerId" />
        </div>
        <div v-if="ready && cacheError" class="mt-3 flex items-center justify-center gap-2 text-xs text-amber-700" role="alert">
          <span>{{ cacheError }}</span>
          <button type="button" class="underline underline-offset-4" @click="draft.retrySave()">重试</button>
        </div>
        <p v-else-if="ready && savingFiles" class="mt-3 text-center text-xs text-slate-400" role="status">正在保存附件…</p>
      </div>
    </div>
    <WorkbookPreview v-if="viewingFile" :local-file="viewingFile" @close="viewingFile = null" />
  </div>
</template>

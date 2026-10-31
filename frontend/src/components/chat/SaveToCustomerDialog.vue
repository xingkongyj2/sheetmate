<script setup lang="ts">
/** 把临时对话保存到客户：选已有客户，或者新建一个客户。会话里的文件、流程、记忆会一起归到客户名下。 */
import { Check, LoaderCircle } from '@lucide/vue'
import { ref } from 'vue'

import { conversationApi } from '@/api'
import { errorText } from '@/api/http'
import Mascot from '@/components/brand/Mascot.vue'
import BaseModal from '@/components/common/BaseModal.vue'
import { useToast } from '@/composables/useToast'
import { useWorkspaceStore } from '@/stores/conversations'

const props = defineProps<{ conversationId: string }>()
const emit = defineEmits<{ close: []; saved: [customerId: string] }>()

const store = useWorkspaceStore()
const toast = useToast()
const tab = ref<'existing' | 'new'>(store.customers.length ? 'existing' : 'new')
const chosen = ref<string | null>(null)
const name = ref('')
const code = ref('')
const description = ref('')
const saving = ref(false)

async function save() {
  saving.value = true
  try {
    const payload = tab.value === 'new'
      ? { new_customer: { name: name.value.trim(), code: code.value.trim(), description: description.value.trim() } }
      : { customer_id: chosen.value ?? undefined }
    const result = await conversationApi.bind(props.conversationId, payload)
    await Promise.all([store.refreshCustomers(), store.refreshConversations()])
    toast.success('已保存到客户，文件、流程和记忆都归到了客户名下')
    emit('saved', result.customer_id)
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <BaseModal title="保存到客户" width="max-w-lg" @close="emit('close')">
    <p class="mb-4 text-sm text-mute">保存后，这次对话上传的文件、生成的处理流程、记下的规则都会归到客户名下，以后这个客户的对话都能用上。</p>
    <div class="mb-4 flex rounded-full bg-paper p-1 text-sm">
      <button class="flex-1 rounded-full py-1.5" :class="tab === 'existing' ? 'bg-white font-medium shadow-sm' : 'text-mute'" @click="tab = 'existing'">已有客户</button>
      <button class="flex-1 rounded-full py-1.5" :class="tab === 'new' ? 'bg-white font-medium shadow-sm' : 'text-mute'" @click="tab = 'new'">新建客户</button>
    </div>

    <div v-if="tab === 'existing'" class="max-h-72 space-y-1 overflow-y-auto">
      <button
        v-for="c in store.customers"
        :key="c.id"
        class="flex w-full items-center gap-3 rounded-xl border px-3 py-2 text-left transition"
        :class="chosen === c.id ? 'border-ink' : 'border-transparent hover:bg-paper'"
        @click="chosen = c.id"
      >
        <Mascot :variant="c.mascot" :size="34" :shadow="false" :track="false" />
        <span class="min-w-0 flex-1">
          <span class="block truncate text-sm font-medium">{{ c.name }}</span>
          <span class="block text-[11px] text-faint">{{ c.recipe_count }} 个流程 · {{ c.file_count }} 个文件</span>
        </span>
        <Check v-if="chosen === c.id" class="size-4" />
      </button>
      <p v-if="!store.customers.length" class="py-6 text-center text-sm text-faint">还没有客户，切换到「新建客户」</p>
    </div>
    <div v-else class="space-y-3">
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-mute">客户名称 <span class="text-error" aria-hidden="true">*</span></span>
        <input v-model="name" class="field" required />
      </label>
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-mute">客户编码</span>
        <input v-model="code" class="field" />
      </label>
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-mute">介绍</span>
        <textarea v-model="description" class="field h-20 resize-none" />
      </label>
    </div>

    <template #footer>
      <button class="btn-line" @click="emit('close')">取消</button>
      <button class="btn-ink" :disabled="saving || (tab === 'existing' ? !chosen : !name.trim())" @click="save">
        <LoaderCircle v-if="saving" class="size-4 animate-spin" />保存
      </button>
    </template>
  </BaseModal>
</template>

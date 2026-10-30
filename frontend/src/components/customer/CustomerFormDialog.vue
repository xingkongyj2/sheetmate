<script setup lang="ts">
/** 新建 / 编辑客户：名称、编码、介绍、IP 形象 */
import { LoaderCircle } from '@lucide/vue'
import { ref } from 'vue'

import { customerApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerCard } from '@/api/types'
import Mascot from '@/components/brand/Mascot.vue'
import { MASCOT_KEYS, MASCOTS } from '@/components/brand/mascots'
import BaseModal from '@/components/common/BaseModal.vue'
import { useToast } from '@/composables/useToast'

const props = defineProps<{ customer?: Pick<CustomerCard, 'id' | 'name' | 'code' | 'description' | 'mascot'> | null }>()
const emit = defineEmits<{ close: []; saved: [customer: CustomerCard] }>()
const toast = useToast()

const name = ref(props.customer?.name ?? '')
const code = ref(props.customer?.code ?? '')
const description = ref(props.customer?.description ?? '')
const mascot = ref(props.customer?.mascot ?? MASCOT_KEYS[Math.floor(Math.random() * MASCOT_KEYS.length)])
const saving = ref(false)

async function save() {
  if (!name.value.trim()) return
  saving.value = true
  try {
    const payload = { name: name.value.trim(), code: code.value.trim(), description: description.value.trim(), mascot: mascot.value }
    const saved = props.customer ? await customerApi.update(props.customer.id, payload) : await customerApi.create(payload)
    if (!props.customer && saved.mascot !== mascot.value) await customerApi.update(saved.id, { mascot: mascot.value })
    emit('saved', { ...saved, mascot: mascot.value })
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <BaseModal :title="customer ? '编辑客户' : '新建客户'" width="max-w-lg" @close="emit('close')">
    <div class="space-y-4">
      <div class="flex justify-center"><Mascot :variant="mascot" :size="96" mood="happy" /></div>
      <div class="flex flex-wrap justify-center gap-1.5">
        <button
          v-for="key in MASCOT_KEYS"
          :key="key"
          class="flex size-11 items-center justify-center overflow-hidden rounded-full border-2 bg-paper transition"
          :class="mascot === key ? 'border-ink' : 'border-transparent hover:border-line'"
          :title="MASCOTS[key].name"
          @click="mascot = key"
        >
          <Mascot :variant="key" :size="44" :shadow="false" :track="false" />
        </button>
      </div>
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-mute">客户名称</span>
        <input v-model="name" class="field" placeholder="例如：顺达物流" />
      </label>
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-mute">客户编码（可选）</span>
        <input v-model="code" class="field" placeholder="例如：SD-001" />
      </label>
      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-mute">介绍（可选）</span>
        <textarea v-model="description" class="field h-20 resize-none" placeholder="这个客户的业务、每月要交付什么…" />
      </label>
    </div>
    <template #footer>
      <button class="btn-line" @click="emit('close')">取消</button>
      <button class="btn-ink" :disabled="!name.trim() || saving" @click="save"><LoaderCircle v-if="saving" class="size-4 animate-spin" />保存</button>
    </template>
  </BaseModal>
</template>

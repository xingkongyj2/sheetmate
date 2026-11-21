<script setup lang="ts">
/** 新建 / 编辑客户：名称、编码、介绍、IP 形象（默认展示 2 排，点击「查看更多」平铺展开） */
import { ChevronDown, ChevronUp, LoaderCircle } from '@lucide/vue'
import { computed, ref } from 'vue'

import { customerApi } from '@/api'
import { errorText } from '@/api/http'
import type { CustomerCard } from '@/api/types'
import Mascot from '@/components/brand/Mascot.vue'
import { MASCOT_KEYS, MASCOTS, PRIMARY_MASCOT_KEYS } from '@/components/brand/mascots'
import BaseModal from '@/components/common/BaseModal.vue'
import { useToast } from '@/composables/useToast'

const props = defineProps<{ customer?: Pick<CustomerCard, 'id' | 'name' | 'code' | 'description' | 'mascot'> | null }>()
const emit = defineEmits<{ close: []; saved: [customer: CustomerCard] }>()
const toast = useToast()

const primarySet = new Set(PRIMARY_MASCOT_KEYS)

const name = ref(props.customer?.name ?? '')
const code = ref(props.customer?.code ?? '')
const description = ref(props.customer?.description ?? '')
const mascot = ref(props.customer?.mascot ?? PRIMARY_MASCOT_KEYS[Math.floor(Math.random() * PRIMARY_MASCOT_KEYS.length)])
// 编辑已有客户时，若当前头像在后排，自动展开定位
const expanded = ref(Boolean(props.customer?.mascot && MASCOT_KEYS.includes(props.customer.mascot) && !primarySet.has(props.customer.mascot)))
const saving = ref(false)
const visibleKeys = computed(() => (expanded.value ? MASCOT_KEYS : PRIMARY_MASCOT_KEYS))

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
      <div class="flex flex-col items-center gap-1.5">
        <Mascot :variant="mascot" :size="96" />
        <span class="text-xs text-faint">{{ MASCOTS[mascot]?.name ?? '墨墨' }}</span>
      </div>

      <!-- 头像选择器：默认展示 2 排（18 款），点「查看更多」直接平铺 9 列展开 -->
      <div class="space-y-2">
        <div class="grid grid-cols-9 justify-items-center gap-1.5">
          <button
            v-for="key in visibleKeys"
            :key="key"
            type="button"
            class="flex size-11 items-center justify-center overflow-hidden rounded-full border-2 bg-paper transition"
            :class="mascot === key ? 'border-ink' : 'border-transparent hover:border-line'"
            :title="MASCOTS[key].name"
            @click="mascot = key"
          >
            <Mascot :variant="key" :size="44" :shadow="false" :track="false" />
          </button>
        </div>

        <div class="flex justify-center pt-0.5">
          <button
            type="button"
            class="inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs text-mute transition hover:bg-paper hover:text-ink"
            :aria-expanded="expanded"
            @click="expanded = !expanded"
          >
            <span>{{ expanded ? '收起' : '查看更多' }}</span>
            <ChevronUp v-if="expanded" class="size-3.5" />
            <ChevronDown v-else class="size-3.5" />
          </button>
        </div>
      </div>

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
      <button class="btn-ink" :disabled="!name.trim() || saving" @click="save"><LoaderCircle v-if="saving" class="size-4 animate-spin" />保存</button>
    </template>
  </BaseModal>
</template>

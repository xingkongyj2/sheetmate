<script setup lang="ts">
/** 新增 / 编辑成员弹窗：账号、姓名、角色（成员 / 管理员）、密码重置、客户权限分配。 */
import { Check, LoaderCircle, Search, ShieldCheck } from '@lucide/vue'
import { computed, ref } from 'vue'

import { adminApi, type AdminCustomerRow, type AdminUserRow } from '@/api'
import { errorText } from '@/api/http'
import Mascot from '@/components/brand/Mascot.vue'
import BaseModal from '@/components/common/BaseModal.vue'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{
  user: AdminUserRow | null
  customers: AdminCustomerRow[]
}>()

const emit = defineEmits<{
  close: []
  saved: [user: AdminUserRow]
}>()

const auth = useAuthStore()
const isEdit = computed(() => Boolean(props.user))
const isSelf = computed(() => props.user?.id === auth.user?.id)

const account = ref(props.user?.account ?? '')
const displayName = ref(props.user?.display_name ?? '')
const role = ref<'admin' | 'member'>(props.user?.role ?? 'member')
const password = ref('')
const selectedCustomers = ref<Set<string>>(new Set(props.user?.customer_ids ?? []))
const customerQuery = ref('')
const saving = ref(false)
const error = ref('')

const filteredCustomers = computed(() => {
  const q = customerQuery.value.trim().toLowerCase()
  if (!q) return props.customers
  return props.customers.filter((c) => `${c.name} ${c.code}`.toLowerCase().includes(q))
})

const ready = computed(() => {
  if (isEdit.value) {
    if (!displayName.value.trim()) return false
    if (password.value && password.value.length < 6) return false
    return true
  }
  return account.value.trim().length >= 3 && password.value.length >= 6
})

function toggleCustomer(id: string) {
  const next = new Set(selectedCustomers.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedCustomers.value = next
}

async function submit() {
  if (!ready.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const saved = isEdit.value
      ? await adminApi.updateUser(props.user!.id, {
          display_name: displayName.value.trim(),
          role: role.value,
          ...(password.value ? { password: password.value } : {}),
          customer_ids: role.value === 'member' ? [...selectedCustomers.value] : props.user!.customer_ids,
        })
      : await adminApi.createUser({
          account: account.value.trim(),
          password: password.value,
          display_name: displayName.value.trim(),
          role: role.value,
          customer_ids: role.value === 'member' ? [...selectedCustomers.value] : [],
        })
    emit('saved', saved)
  } catch (err) {
    error.value = errorText(err)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <BaseModal :title="isEdit ? '编辑成员' : '新增成员'" width="max-w-md" @close="!saving && emit('close')">
    <form class="space-y-4" @submit.prevent="submit">
      <div class="grid gap-3.5 sm:grid-cols-2">
        <label class="block">
          <span class="mb-1.5 block text-xs font-medium text-mute">账号</span>
          <input
            v-model="account"
            class="field h-10 py-0 text-sm"
            :disabled="isEdit || saving"
            placeholder="至少 3 个字符"
            autocomplete="off"
            :autofocus="!isEdit"
          />
        </label>
        <label class="block">
          <span class="mb-1.5 block text-xs font-medium text-mute">姓名</span>
          <input
            v-model="displayName"
            class="field h-10 py-0 text-sm"
            maxlength="40"
            :disabled="saving"
            placeholder="显示名称"
            :autofocus="isEdit"
          />
        </label>
      </div>

      <div>
        <span class="mb-1.5 block text-xs font-medium text-mute">角色</span>
        <div class="grid grid-cols-2 gap-1 rounded-xl bg-paper p-1" role="radiogroup" aria-label="角色">
          <button
            type="button"
            class="rounded-lg py-2 text-sm transition"
            :class="role === 'member' ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
            :disabled="saving || isSelf"
            @click="role = 'member'"
          >
            成员
          </button>
          <button
            type="button"
            class="flex items-center justify-center gap-1.5 rounded-lg py-2 text-sm transition"
            :class="role === 'admin' ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
            :disabled="saving"
            @click="role = 'admin'"
          >
            <ShieldCheck class="size-3.5" />管理员
          </button>
        </div>
      </div>

      <label class="block">
        <span class="mb-1.5 block text-xs font-medium text-mute">{{ isEdit ? '重置密码' : '初始密码' }}</span>
        <input
          v-model="password"
          type="password"
          class="field h-10 py-0 text-sm"
          autocomplete="new-password"
          :disabled="saving"
          :placeholder="isEdit ? '留空不修改，至少 6 位' : '至少 6 位'"
        />
      </label>

      <!-- 客户权限分配 -->
      <div>
        <div class="mb-1.5 flex items-center justify-between">
          <span class="text-xs font-medium text-mute">客户权限</span>
          <span v-if="role === 'member'" class="text-[11px] tabular-nums text-faint">已选 {{ selectedCustomers.size }}</span>
        </div>

        <div
          v-if="role === 'admin'"
          class="flex items-center gap-2 rounded-xl bg-paper px-3.5 py-2.5 text-xs text-mute"
        >
          <ShieldCheck class="size-3.5 shrink-0 text-ink" />
          <span>管理员默认可访问全部客户与后台</span>
        </div>

        <div v-else class="overflow-hidden rounded-xl border border-line bg-white">
          <label v-if="customers.length > 5" class="flex items-center gap-2 border-b border-line px-3 py-2">
            <Search class="size-3.5 shrink-0 text-faint" />
            <input
              v-model="customerQuery"
              placeholder="筛选客户"
              class="w-full bg-transparent text-xs outline-none placeholder:text-faint"
            />
          </label>
          <div v-if="!customers.length" class="py-6 text-center text-xs text-faint">暂无客户</div>
          <div v-else class="max-h-44 divide-y divide-line/60 overflow-y-auto">
            <button
              v-for="c in filteredCustomers"
              :key="c.id"
              type="button"
              class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm transition hover:bg-paper/70"
              @click="toggleCustomer(c.id)"
            >
              <span
                class="flex size-4 shrink-0 items-center justify-center rounded border transition"
                :class="selectedCustomers.has(c.id) ? 'border-ink bg-ink text-white' : 'border-line bg-white'"
              >
                <Check v-if="selectedCustomers.has(c.id)" class="size-3" />
              </span>
              <Mascot :variant="c.mascot" :size="22" :shadow="false" :track="false" mood="idle" />
              <span class="min-w-0 flex-1 truncate text-[13px]">{{ c.name }}</span>
              <span v-if="c.code" class="shrink-0 font-mono text-[11px] text-faint">{{ c.code }}</span>
            </button>
          </div>
        </div>
      </div>

      <p v-if="error" class="rounded-xl bg-error/5 px-3 py-2 text-xs text-error" role="alert">{{ error }}</p>

      <div class="flex justify-end gap-2 pt-2">
        <button type="button" class="btn-line h-9 text-sm" :disabled="saving" @click="emit('close')">取消</button>
        <button type="submit" class="btn-ink h-9 text-sm disabled:opacity-40" :disabled="!ready || saving">
          <LoaderCircle v-if="saving" class="size-4 animate-spin" />{{ isEdit ? '保存' : '创建' }}
        </button>
      </div>
    </form>
  </BaseModal>
</template>

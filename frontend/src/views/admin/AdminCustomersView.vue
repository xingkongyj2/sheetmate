<script setup lang="ts">
/** 后台客户与流程管理：查看全部客户资产、分配成员权限、跳转工作台或清理客户。 */
import { ArrowUpRight, Building2, Check, LoaderCircle, Plus, Search, Trash2, UserPlus } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { adminApi, customerApi, type AdminCustomerRow, type AdminUserRow } from '@/api'
import { errorText } from '@/api/http'
import Mascot from '@/components/brand/Mascot.vue'
import BaseModal from '@/components/common/BaseModal.vue'
import CustomerFormDialog from '@/components/customer/CustomerFormDialog.vue'
import { shortTime } from '@/composables/conversationGroups'
import { executionFileSize } from '@/composables/executionPresentation'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'

const toast = useToast()
const { confirmDelete } = useConfirm()

const customers = ref<AdminCustomerRow[]>([])
const users = ref<AdminUserRow[]>([])
const loading = ref(true)
const error = ref('')
const query = ref('')
const creating = ref(false)

// 分配成员弹窗状态
const assigningCustomer = ref<AdminCustomerRow | null>(null)
const selectedMemberIds = ref<Set<number>>(new Set())
const savingMembers = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [cList, uList] = await Promise.all([adminApi.listCustomers(), adminApi.listUsers()])
    customers.value = cList
    users.value = uList
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)

const assignableUsers = computed(() => users.value.filter((u) => u.role !== 'admin'))

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return customers.value
  return customers.value.filter((c) => `${c.name} ${c.code} ${c.creator?.display_name ?? ''}`.toLowerCase().includes(q))
})

function openAssign(customer: AdminCustomerRow) {
  assigningCustomer.value = customer
  selectedMemberIds.value = new Set(customer.member_ids)
}

function toggleMember(id: number) {
  const next = new Set(selectedMemberIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedMemberIds.value = next
}

async function saveMembers() {
  if (!assigningCustomer.value || savingMembers.value) return
  savingMembers.value = true
  try {
    const updated = await adminApi.setCustomerMembers(assigningCustomer.value.id, [...selectedMemberIds.value])
    const idx = customers.value.findIndex((c) => c.id === updated.id)
    if (idx >= 0) customers.value[idx] = updated
    assigningCustomer.value = null
    toast.success('已更新成员权限')
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    savingMembers.value = false
  }
}

async function onCustomerCreated() {
  creating.value = false
  await load()
  toast.success('已新建客户')
}

async function removeCustomer(c: AdminCustomerRow) {
  if (
    !(await confirmDelete({
      title: '删除客户？',
      target: c.name,
      description: '该客户下的流程、公式、文件与记忆都会删除，删除后无法恢复。',
      confirmText: '删除客户',
      action: () => customerApi.remove(c.id),
    }))
  ) {
    return
  }
  customers.value = customers.value.filter((item) => item.id !== c.id)
  toast.success('已删除')
}

const initialOf = (name: string) => (name || '?').slice(0, 1).toUpperCase()
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[1200px] px-5 py-6 sm:px-8 sm:py-8">
      <header class="flex flex-wrap items-center gap-3">
        <div class="mr-auto flex items-baseline gap-2.5">
          <h1 class="text-2xl font-semibold tracking-tight text-ink">客户与流程</h1>
          <span class="text-xs tabular-nums text-faint">{{ customers.length }} 个</span>
        </div>

        <label class="flex h-9 w-full items-center gap-2 rounded-xl border border-line bg-white px-3 sm:w-56">
          <Search class="size-3.5 shrink-0 text-faint" />
          <input
            v-model="query"
            placeholder="搜索客户名称或编号"
            aria-label="搜索客户"
            class="w-full min-w-0 bg-transparent text-[13px] outline-none placeholder:text-faint"
          />
        </label>

        <button type="button" class="btn-ink h-9 gap-1.5 px-3.5 text-[13px]" @click="creating = true">
          <Plus class="size-4" />新建客户
        </button>
      </header>

      <div v-if="error" class="card-soft mt-6 flex items-center gap-3 p-5 text-sm text-error" role="alert">
        <span>{{ error }}</span>
        <button type="button" class="font-medium underline" @click="load">重试</button>
      </div>

      <div v-else-if="loading" class="flex justify-center py-24">
        <LoaderCircle class="size-6 animate-spin text-mute" />
      </div>

      <div v-else-if="!filtered.length" class="card-soft mt-6 flex flex-col items-center gap-2.5 py-16 text-center">
        <span class="flex size-11 items-center justify-center rounded-2xl bg-paper text-mute">
          <Building2 class="size-5" />
        </span>
        <p class="text-sm text-mute">{{ customers.length ? '没有匹配的客户' : '还没有客户' }}</p>
      </div>

      <div v-else class="card-soft mt-6 overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full min-w-[780px] border-collapse text-left">
            <thead>
              <tr class="border-b border-line text-[11px] font-medium text-faint">
                <th class="py-3 pl-5 pr-3 font-medium">客户</th>
                <th class="px-3 py-3 font-medium">创建人</th>
                <th class="px-3 py-3 font-medium">授权成员</th>
                <th class="px-3 py-3 font-medium">流程 / 公式</th>
                <th class="px-3 py-3 font-medium">对话 / 文件</th>
                <th class="px-3 py-3 font-medium">最近活跃</th>
                <th class="py-3 pl-3 pr-5 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-line/60 text-sm">
              <tr v-for="c in filtered" :key="c.id" class="group transition-colors hover:bg-paper/55">
                <td class="py-3.5 pl-5 pr-3">
                  <div class="flex items-center gap-3">
                    <Mascot :variant="c.mascot" :size="30" :shadow="false" :track="false" mood="idle" />
                    <div class="min-w-0">
                      <p class="truncate font-medium text-ink">{{ c.name }}</p>
                      <p v-if="c.code" class="truncate font-mono text-[11px] text-faint">{{ c.code }}</p>
                    </div>
                  </div>
                </td>

                <td class="px-3 py-3.5 text-xs text-mute">
                  {{ c.creator?.display_name || '系统' }}
                </td>

                <td class="px-3 py-3.5">
                  <button
                    type="button"
                    class="inline-flex flex-wrap items-center gap-1.5 rounded-lg text-left transition hover:opacity-80"
                    @click="openAssign(c)"
                  >
                    <template v-if="c.members.length">
                      <span
                        v-for="m in c.members.slice(0, 2)"
                        :key="m.id"
                        class="inline-flex items-center gap-1 rounded-full bg-paper px-2 py-0.5 text-xs text-ink"
                      >
                        {{ m.display_name }}
                      </span>
                      <span v-if="c.members.length > 2" class="text-xs tabular-nums text-faint">+{{ c.members.length - 2 }}</span>
                    </template>
                    <span v-else class="text-xs text-faint">仅管理员与创建人</span>
                  </button>
                </td>

                <td class="px-3 py-3.5 text-xs tabular-nums text-mute">
                  {{ c.recipe_count }} 流程 · {{ c.formula_count }} 公式
                </td>

                <td class="px-3 py-3.5 text-xs tabular-nums text-mute">
                  {{ c.conversation_count }} 对话 · {{ c.file_count }} 文件
                  <span v-if="c.storage_bytes" class="text-faint">({{ executionFileSize(c.storage_bytes) }})</span>
                </td>

                <td class="px-3 py-3.5 text-xs tabular-nums text-faint">
                  {{ shortTime(c.last_activity) || '—' }}
                </td>

                <td class="py-3.5 pl-3 pr-5">
                  <div class="flex items-center justify-end gap-1">
                    <button
                      type="button"
                      class="icon-btn size-8"
                      title="分配成员"
                      :aria-label="`为 ${c.name} 分配成员`"
                      @click="openAssign(c)"
                    >
                      <UserPlus class="size-3.5" />
                    </button>
                    <RouterLink
                      :to="{ name: 'customer', params: { id: c.id } }"
                      class="icon-btn size-8"
                      title="打开客户详情"
                      :aria-label="`打开 ${c.name}`"
                    >
                      <ArrowUpRight class="size-3.5" />
                    </RouterLink>
                    <button
                      type="button"
                      class="icon-btn size-8 hover:text-error"
                      title="删除客户"
                      :aria-label="`删除 ${c.name}`"
                      @click="removeCustomer(c)"
                    >
                      <Trash2 class="size-3.5" />
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 分配成员弹窗 -->
    <BaseModal
      v-if="assigningCustomer"
      :title="`成员权限 · ${assigningCustomer.name}`"
      width="max-w-sm"
      @close="!savingMembers && (assigningCustomer = null)"
    >
      <div class="space-y-4">
        <div v-if="!assignableUsers.length" class="rounded-xl bg-paper py-8 text-center text-xs text-mute">
          暂无普通成员（管理员默认拥有全部权限）
        </div>
        <div v-else class="max-h-64 divide-y divide-line/60 overflow-y-auto rounded-xl border border-line bg-white">
          <button
            v-for="u in assignableUsers"
            :key="u.id"
            type="button"
            class="flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition hover:bg-paper/70"
            @click="toggleMember(u.id)"
          >
            <span
              class="flex size-4 shrink-0 items-center justify-center rounded border transition"
              :class="selectedMemberIds.has(u.id) ? 'border-ink bg-ink text-white' : 'border-line bg-white'"
            >
              <Check v-if="selectedMemberIds.has(u.id)" class="size-3" />
            </span>
            <span class="flex size-7 shrink-0 items-center justify-center overflow-hidden rounded-full bg-ink text-[11px] font-semibold text-white">
              <img v-if="u.avatar" :src="u.avatar" alt="" class="size-full object-cover" />
              <template v-else>{{ initialOf(u.display_name) }}</template>
            </span>
            <div class="min-w-0 flex-1">
              <p class="truncate text-sm font-medium text-ink">{{ u.display_name }}</p>
              <p class="truncate font-mono text-[11px] text-faint">@{{ u.account }}</p>
            </div>
          </button>
        </div>

        <div class="flex justify-end gap-2">
          <button type="button" class="btn-line h-9 text-sm" :disabled="savingMembers" @click="assigningCustomer = null">取消</button>
          <button type="button" class="btn-ink h-9 text-sm" :disabled="savingMembers" @click="saveMembers">
            <LoaderCircle v-if="savingMembers" class="size-4 animate-spin" />保存
          </button>
        </div>
      </div>
    </BaseModal>

    <CustomerFormDialog v-if="creating" @close="creating = false" @saved="onCustomerCreated" />
  </div>
</template>

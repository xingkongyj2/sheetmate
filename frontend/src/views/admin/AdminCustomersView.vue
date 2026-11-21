<script setup lang="ts">
/** 后台客户与流程管理：查看全部客户资产、分配成员权限、跳转工作台或清理客户。 */
import { ArrowUpRight, Building2, Check, LoaderCircle, Plus, Search, Trash2, UserPlus, X } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { adminApi, customerApi, type AdminCustomerRow, type AdminUserRow } from '@/api'
import { errorText } from '@/api/http'
import Mascot from '@/components/brand/Mascot.vue'
import MascotLoader from '@/components/brand/MascotLoader.vue'
import UserAvatar from '@/components/brand/UserAvatar.vue'
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
const memberQuery = ref('')
const memberScope = ref<'all' | 'selected'>('all')
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

const filteredAssignableUsers = computed(() => {
  const q = memberQuery.value.trim().toLowerCase()
  return assignableUsers.value.filter((u) => {
    if (memberScope.value === 'selected' && !selectedMemberIds.value.has(u.id)) return false
    if (!q) return true
    return `${u.display_name} ${u.account}`.toLowerCase().includes(q)
  })
})

const nonAdminMembers = (c: AdminCustomerRow) => c.members.filter((m) => m.role !== 'admin')

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return customers.value
  return customers.value.filter((c) => `${c.name} ${c.code} ${c.creator?.display_name ?? ''}`.toLowerCase().includes(q))
})

function openAssign(customer: AdminCustomerRow) {
  assigningCustomer.value = customer
  selectedMemberIds.value = new Set(nonAdminMembers(customer).map((m) => m.id))
  memberQuery.value = ''
  memberScope.value = 'all'
}

function clearSelectedMembers() {
  selectedMemberIds.value = new Set()
  if (memberScope.value === 'selected') memberScope.value = 'all'
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
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[1200px] px-4 py-6 sm:px-8 sm:py-7">
      <header class="flex flex-wrap items-center gap-3">
        <div class="mr-auto flex items-baseline gap-2.5">
          <h1 class="text-xl font-semibold tracking-tight">客户管理</h1>
          <span class="text-xs tabular-nums text-faint">{{ customers.length }} 个</span>
        </div>

        <label class="flex h-8 w-full items-center gap-2 rounded-full bg-paper px-3 sm:w-52">
          <Search class="size-3.5 shrink-0 text-faint" />
          <input
            v-model="query"
            placeholder="搜索客户名称或编号"
            aria-label="搜索客户"
            class="w-full min-w-0 bg-transparent text-[13px] outline-none placeholder:text-faint"
          />
        </label>

        <button type="button" class="btn-ink h-8 gap-1.5 rounded-full px-3.5 text-[13px]" @click="creating = true">
          <Plus class="size-4" />新建客户
        </button>
      </header>

      <div v-if="error" class="card-soft mt-6 flex items-center gap-3 p-5 text-sm text-error" role="alert">
        <span>{{ error }}</span>
        <button type="button" class="font-medium underline" @click="load">重试</button>
      </div>

      <MascotLoader v-else-if="loading" size="lg" class="py-24" />

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
                    <template v-if="nonAdminMembers(c).length">
                      <span
                        v-for="m in nonAdminMembers(c).slice(0, 2)"
                        :key="m.id"
                        class="inline-flex items-center gap-1 rounded-full bg-paper px-2 py-0.5 text-xs text-ink"
                      >
                        {{ m.display_name }}
                      </span>
                      <span v-if="nonAdminMembers(c).length > 2" class="text-xs tabular-nums text-faint">+{{ nonAdminMembers(c).length - 2 }}</span>
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
      width="max-w-md"
      @close="!savingMembers && (assigningCustomer = null)"
    >
      <div class="space-y-3.5">
        <div v-if="!assignableUsers.length" class="rounded-xl bg-paper py-8 text-center text-xs text-mute">
          暂无普通成员（管理员默认拥有全部权限）
        </div>
        <template v-else>
          <div class="flex items-center justify-between gap-2 text-[11px]">
            <div class="flex rounded-full bg-paper p-0.5">
              <button
                type="button"
                class="rounded-full px-2.5 py-0.5 transition"
                :class="memberScope === 'all' ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
                @click="memberScope = 'all'"
              >全部 {{ assignableUsers.length }}</button>
              <button
                type="button"
                class="rounded-full px-2.5 py-0.5 tabular-nums transition"
                :class="memberScope === 'selected' ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
                @click="memberScope = 'selected'"
              >已选 {{ selectedMemberIds.size }}</button>
            </div>
            <button
              v-if="selectedMemberIds.size"
              type="button"
              class="text-faint transition hover:text-ink"
              @click="clearSelectedMembers"
            >清空</button>
          </div>

          <div class="overflow-hidden rounded-xl border border-line bg-white">
            <div class="flex items-center gap-2 border-b border-line px-3 py-2">
              <Search class="size-3.5 shrink-0 text-faint" />
              <input
                v-model="memberQuery"
                placeholder="搜索姓名或账号"
                aria-label="搜索成员"
                class="w-full min-w-0 bg-transparent text-xs outline-none placeholder:text-faint"
              />
              <button
                v-if="memberQuery"
                type="button"
                class="rounded p-0.5 text-faint transition hover:text-ink"
                aria-label="清空搜索"
                @click="memberQuery = ''"
              >
                <X class="size-3" />
              </button>
            </div>
            <div v-if="!filteredAssignableUsers.length" class="py-8 text-center text-xs text-faint">
              {{ memberScope === 'selected' && !selectedMemberIds.size ? '暂未选择成员' : '没有匹配的成员' }}
            </div>
            <div v-else class="max-h-72 divide-y divide-line/60 overflow-y-auto">
              <button
                v-for="u in filteredAssignableUsers"
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
                <UserAvatar :seed="u.account || u.display_name" :src="u.avatar" class="size-7" />
                <div class="min-w-0 flex-1">
                  <p class="truncate text-sm font-medium text-ink">{{ u.display_name }}</p>
                  <p class="truncate font-mono text-[11px] text-faint">@{{ u.account }}</p>
                </div>
              </button>
            </div>
          </div>
        </template>

        <div class="flex justify-end gap-2 pt-1">
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

<script setup lang="ts">
/** 后台人员管理：成员列表、角色切换（设为/取消管理员）、客户权限分配、新增/编辑/重置密码/删除。 */
import { LoaderCircle, Pencil, Plus, Search, ShieldCheck, ShieldOff, Trash2, Users } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'

import { adminApi, type AdminCustomerRow, type AdminUserRow } from '@/api'
import { errorText } from '@/api/http'
import UserFormModal from '@/components/admin/UserFormModal.vue'
import Mascot from '@/components/brand/Mascot.vue'
import UserAvatar from '@/components/brand/UserAvatar.vue'
import MascotLoader from '@/components/brand/MascotLoader.vue'
import { shortTime } from '@/composables/conversationGroups'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const toast = useToast()
const { confirmDelete } = useConfirm()

const users = ref<AdminUserRow[]>([])
const customers = ref<AdminCustomerRow[]>([])
const loading = ref(true)
const error = ref('')
const query = ref('')
const roleFilter = ref<'all' | 'admin' | 'member'>('all')
const busyId = ref<number | null>(null)
const modalOpen = ref(false)
const editingUser = ref<AdminUserRow | null>(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [uList, cList] = await Promise.all([adminApi.listUsers(), adminApi.listCustomers()])
    users.value = uList
    customers.value = cList
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)

const adminCount = computed(() => users.value.filter((u) => u.role === 'admin').length)
const memberCount = computed(() => users.value.length - adminCount.value)

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return users.value.filter((u) => {
    if (roleFilter.value !== 'all' && u.role !== roleFilter.value) return false
    if (!q) return true
    return `${u.display_name} ${u.account}`.toLowerCase().includes(q)
  })
})

function openCreate() {
  editingUser.value = null
  modalOpen.value = true
}

function openEdit(user: AdminUserRow) {
  editingUser.value = user
  modalOpen.value = true
}

function onSaved(saved: AdminUserRow) {
  const idx = users.value.findIndex((u) => u.id === saved.id)
  if (idx >= 0) users.value[idx] = saved
  else users.value.push(saved)
  modalOpen.value = false
  toast.success(editingUser.value ? '已更新成员信息' : '已新增成员')
}

async function toggleAdminRole(user: AdminUserRow) {
  if (busyId.value !== null || user.id === auth.user?.id) return
  const nextRole = user.role === 'admin' ? 'member' : 'admin'
  busyId.value = user.id
  try {
    const updated = await adminApi.updateUser(user.id, { role: nextRole })
    const idx = users.value.findIndex((u) => u.id === user.id)
    if (idx >= 0) users.value[idx] = updated
    toast.success(nextRole === 'admin' ? `已将「${user.display_name}」设为管理员` : `已取消「${user.display_name}」的管理员权限`)
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    busyId.value = null
  }
}

async function removeUser(user: AdminUserRow) {
  if (user.id === auth.user?.id) return
  if (
    !(await confirmDelete({
      title: '删除成员？',
      target: `${user.display_name}（${user.account}）`,
      description: '删除后该成员将无法登录，其会话与任务记录也会随之移除。',
      confirmText: '删除成员',
      action: () => adminApi.deleteUser(user.id),
    }))
  ) {
    return
  }
  users.value = users.value.filter((u) => u.id !== user.id)
  toast.success('已删除')
}

const shortDate = (iso: string) => (iso ? iso.slice(0, 10) : '—')
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-[1200px] px-4 py-6 sm:px-8 sm:py-7">
      <!-- 顶部栏 -->
      <header class="flex flex-wrap items-center gap-3">
        <div class="mr-auto flex items-baseline gap-2.5">
          <h1 class="text-xl font-semibold tracking-tight">人员管理</h1>
          <span class="text-xs tabular-nums text-faint">{{ users.length }} 人</span>
        </div>

        <label class="flex h-8 w-full items-center gap-2 rounded-full bg-paper px-3 sm:w-48">
          <Search class="size-3.5 shrink-0 text-faint" />
          <input
            v-model="query"
            placeholder="搜索姓名或账号"
            aria-label="搜索成员"
            class="w-full min-w-0 bg-transparent text-[13px] outline-none placeholder:text-faint"
          />
        </label>

        <div class="flex gap-0.5 rounded-full bg-paper p-0.5 text-xs" role="tablist" aria-label="角色筛选">
          <button
            type="button"
            role="tab"
            :aria-selected="roleFilter === 'all'"
            class="rounded-full px-2.5 py-1 transition"
            :class="roleFilter === 'all' ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
            @click="roleFilter = 'all'"
          >全部 {{ users.length }}</button>
          <button
            type="button"
            role="tab"
            :aria-selected="roleFilter === 'admin'"
            class="rounded-full px-2.5 py-1 transition"
            :class="roleFilter === 'admin' ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
            @click="roleFilter = 'admin'"
          >管理员 {{ adminCount }}</button>
          <button
            type="button"
            role="tab"
            :aria-selected="roleFilter === 'member'"
            class="rounded-full px-2.5 py-1 transition"
            :class="roleFilter === 'member' ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
            @click="roleFilter = 'member'"
          >成员 {{ memberCount }}</button>
        </div>

        <button type="button" class="btn-ink h-8 gap-1.5 rounded-full px-3.5 text-[13px]" @click="openCreate">
          <Plus class="size-4" />新增成员
        </button>
      </header>

      <div v-if="error" class="card-soft mt-6 flex items-center gap-3 p-5 text-sm text-error" role="alert">
        <span>{{ error }}</span>
        <button type="button" class="font-medium underline" @click="load">重试</button>
      </div>

      <MascotLoader v-else-if="loading" size="lg" class="py-24" />

      <div v-else-if="!filtered.length" class="card-soft mt-6 flex flex-col items-center gap-2.5 py-16 text-center">
        <span class="flex size-11 items-center justify-center rounded-2xl bg-paper text-mute">
          <Users class="size-5" />
        </span>
        <p class="text-sm text-mute">没有匹配的成员</p>
      </div>

      <!-- 成员列表 -->
      <div v-else class="card-soft mt-6 overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full min-w-[740px] border-collapse text-left">
            <thead>
              <tr class="border-b border-line text-[11px] font-medium text-faint">
                <th class="py-3 pl-5 pr-3 font-medium">成员</th>
                <th class="px-3 py-3 font-medium">角色</th>
                <th class="px-3 py-3 font-medium">客户权限</th>
                <th class="px-3 py-3 font-medium">使用量</th>
                <th class="px-3 py-3 font-medium">最近活跃</th>
                <th class="px-3 py-3 font-medium">创建时间</th>
                <th class="py-3 pl-3 pr-5 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-line/60 text-sm">
              <tr
                v-for="u in filtered"
                :key="u.id"
                class="group transition-colors hover:bg-paper/55"
              >
                <td class="py-3.5 pl-5 pr-3">
                  <div class="flex items-center gap-3">
                    <UserAvatar :seed="u.account || u.display_name" :src="u.avatar" class="size-9" />
                    <div class="min-w-0">
                      <div class="flex items-center gap-1.5">
                        <span class="truncate font-medium text-ink">{{ u.display_name }}</span>
                        <span
                          v-if="u.id === auth.user?.id"
                          class="rounded-full bg-paper px-1.5 py-px text-[10px] font-medium text-mute"
                        >我</span>
                      </div>
                      <p class="truncate font-mono text-xs text-faint">@{{ u.account }}</p>
                    </div>
                  </div>
                </td>

                <td class="px-3 py-3.5">
                  <span
                    class="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium"
                    :class="u.role === 'admin' ? 'bg-ink text-white' : 'bg-paper text-mute'"
                  >
                    <ShieldCheck v-if="u.role === 'admin'" class="size-3" />
                    {{ u.role_label }}
                  </span>
                </td>

                <td class="px-3 py-3.5">
                  <span v-if="u.role === 'admin'" class="text-xs text-mute">全部客户</span>
                  <div v-else-if="u.customers.length" class="flex flex-wrap items-center gap-1.5">
                    <span
                      v-for="c in u.customers.slice(0, 2)"
                      :key="c.id"
                      class="inline-flex items-center gap-1 rounded-full border border-line bg-white px-2 py-0.5 text-xs text-ink"
                    >
                      <Mascot :variant="c.mascot" :size="14" :shadow="false" :track="false" mood="idle" />
                      <span class="max-w-24 truncate">{{ c.name }}</span>
                    </span>
                    <span v-if="u.customers.length > 2" class="text-xs tabular-nums text-faint">+{{ u.customers.length - 2 }}</span>
                  </div>
                  <button
                    v-else
                    type="button"
                    class="text-xs text-faint transition hover:text-ink"
                    @click="openEdit(u)"
                  >
                    {{ u.owned_customer_count ? `自建 ${u.owned_customer_count} 个` : '未分配' }}
                  </button>
                </td>

                <td class="px-3 py-3.5 text-xs tabular-nums text-mute">
                  {{ u.conversation_count }} 对话 · {{ u.task_count }} 任务
                </td>

                <td class="px-3 py-3.5 text-xs tabular-nums text-faint">
                  {{ shortTime(u.last_active_at) || '—' }}
                </td>

                <td class="px-3 py-3.5 text-xs tabular-nums text-faint">
                  {{ shortDate(u.created_at) }}
                </td>

                <td class="py-3.5 pl-3 pr-5">
                  <div class="flex items-center justify-end gap-1">
                    <button
                      type="button"
                      class="icon-btn size-8 disabled:opacity-30"
                      :title="u.role === 'admin' ? '取消管理员' : '设为管理员'"
                      :aria-label="u.role === 'admin' ? `取消 ${u.display_name} 的管理员` : `将 ${u.display_name} 设为管理员`"
                      :disabled="u.id === auth.user?.id || busyId === u.id || (u.role === 'admin' && adminCount <= 1)"
                      @click="toggleAdminRole(u)"
                    >
                      <LoaderCircle v-if="busyId === u.id" class="size-3.5 animate-spin" />
                      <ShieldOff v-else-if="u.role === 'admin'" class="size-3.5" />
                      <ShieldCheck v-else class="size-3.5" />
                    </button>
                    <button
                      type="button"
                      class="icon-btn size-8"
                      title="编辑成员"
                      :aria-label="`编辑 ${u.display_name}`"
                      @click="openEdit(u)"
                    >
                      <Pencil class="size-3.5" />
                    </button>
                    <button
                      type="button"
                      class="icon-btn size-8 hover:text-error disabled:opacity-30"
                      title="删除成员"
                      :aria-label="`删除 ${u.display_name}`"
                      :disabled="u.id === auth.user?.id || (u.role === 'admin' && adminCount <= 1)"
                      @click="removeUser(u)"
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

    <UserFormModal
      v-if="modalOpen"
      :user="editingUser"
      :customers="customers"
      @close="modalOpen = false"
      @saved="onSaved"
    />
  </div>
</template>

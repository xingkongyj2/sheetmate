<script setup lang="ts">
import { Camera, Check, KeyRound, LoaderCircle, Pencil, Trash2, X } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import { authApi } from '@/api'
import { errorText } from '@/api/http'
import UserAvatar from '@/components/brand/UserAvatar.vue'
import BaseModal from '@/components/common/BaseModal.vue'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'

/** 个人资料：头像、名字、密码 */
const auth = useAuthStore()
const toast = useToast()

const AVATAR_SIZE = 256
const name = ref(auth.user?.display_name ?? '')
const editingName = ref(false)
const savingName = ref(false)
const savingAvatar = ref(false)
const changingPassword = ref(false)
const savingPassword = ref(false)
const current = ref('')
const next = ref('')
const again = ref('')
const passwordError = ref('')

watch(() => auth.user?.display_name, (value) => { if (!editingName.value) name.value = value ?? '' })

const avatar = computed(() => auth.user?.avatar ?? '')
const avatarKey = computed(() => auth.user?.account || auth.user?.display_name || '')
const nameChanged = computed(() => !!name.value.trim() && name.value.trim() !== auth.user?.display_name)
const passwordReady = computed(() => !!current.value && next.value.length >= 6 && next.value === again.value)

/** 图片居中裁成正方形、缩到 256 像素：存起来只有 20KB 左右 */
async function toAvatar(file: File): Promise<string> {
  const url = URL.createObjectURL(file)
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image()
      el.onload = () => resolve(el)
      el.onerror = () => reject(new Error('这张图片打不开，请换一张 PNG / JPG / WebP 图片'))
      el.src = url
    })
    const side = Math.min(image.naturalWidth, image.naturalHeight)
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = AVATAR_SIZE
    const ctx = canvas.getContext('2d')!
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, AVATAR_SIZE, AVATAR_SIZE)
    ctx.drawImage(image, (image.naturalWidth - side) / 2, (image.naturalHeight - side) / 2, side, side, 0, 0, AVATAR_SIZE, AVATAR_SIZE)
    return canvas.toDataURL('image/jpeg', 0.85)
  } finally {
    URL.revokeObjectURL(url)
  }
}

async function pickAvatar(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || savingAvatar.value) return
  savingAvatar.value = true
  try {
    await auth.updateProfile({ avatar: await toAvatar(file) })
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    savingAvatar.value = false
  }
}

async function removeAvatar() {
  if (savingAvatar.value) return
  savingAvatar.value = true
  try {
    await auth.updateProfile({ avatar: '' })
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    savingAvatar.value = false
  }
}

function startEditName() {
  name.value = auth.user?.display_name ?? ''
  editingName.value = true
}

function cancelEditName() {
  editingName.value = false
  name.value = auth.user?.display_name ?? ''
}

async function saveName() {
  if (savingName.value) return
  if (!nameChanged.value) {
    cancelEditName()
    return
  }
  savingName.value = true
  try {
    await auth.updateProfile({ display_name: name.value.trim() })
    editingName.value = false
    toast.success('已保存')
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    savingName.value = false
  }
}

function closePassword() {
  changingPassword.value = false
  current.value = next.value = again.value = passwordError.value = ''
}

async function savePassword() {
  if (!passwordReady.value || savingPassword.value) return
  savingPassword.value = true
  passwordError.value = ''
  try {
    await authApi.changePassword(current.value, next.value)
    toast.success('密码已修改')
    closePassword()
  } catch (err) {
    passwordError.value = errorText(err)
  } finally {
    savingPassword.value = false
  }
}
</script>

<template>
  <section class="card-soft p-5 sm:p-6" aria-label="个人资料" data-testid="profile-card">
    <div class="flex items-center gap-4 sm:gap-5">
      <div class="group relative size-16 shrink-0">
        <label class="block size-16 cursor-pointer overflow-hidden rounded-full" title="更换头像">
          <UserAvatar :seed="avatarKey" :src="avatar" alt="头像" class="size-full" />
          <span class="absolute inset-0 flex items-center justify-center rounded-full bg-black/40 text-white opacity-0 transition group-hover:opacity-100 group-focus-within:opacity-100">
            <LoaderCircle v-if="savingAvatar" class="size-5 animate-spin" /><Camera v-else class="size-5" />
          </span>
          <input type="file" accept="image/png,image/jpeg,image/webp" class="sr-only" aria-label="更换头像" :disabled="savingAvatar" data-testid="avatar-input" @change="pickAvatar" />
        </label>
        <button v-if="avatar" type="button" class="icon-btn absolute -bottom-1 -right-1 size-6 rounded-full border border-line bg-white shadow-sm hover:text-error" title="移除头像" aria-label="移除头像" :disabled="savingAvatar" @click="removeAvatar"><Trash2 class="size-3.5" /></button>
      </div>

      <form v-if="editingName" class="flex min-w-0 flex-1 items-center gap-2" @submit.prevent="saveName" @keydown.esc="cancelEditName">
        <input v-model="name" class="field h-10 min-w-0 flex-1 py-0" maxlength="40" aria-label="名字" placeholder="名字" autofocus data-testid="profile-name" />
        <button type="submit" class="btn-ink size-10 shrink-0 !p-0 disabled:opacity-40" title="保存" aria-label="保存名字" :disabled="!nameChanged || savingName">
          <LoaderCircle v-if="savingName" class="size-4 animate-spin" /><Check v-else class="size-4" />
        </button>
        <button type="button" class="icon-btn size-10 shrink-0 border border-line" title="取消" aria-label="取消修改名字" :disabled="savingName" @click="cancelEditName"><X class="size-4" /></button>
      </form>
      <template v-else>
        <div class="min-w-0 flex-1">
          <p class="truncate text-lg font-semibold leading-tight" data-testid="profile-display-name">{{ auth.user?.display_name }}</p>
          <p class="mt-1 truncate text-sm text-mute" data-testid="profile-role">{{ auth.user?.role_label }}</p>
        </div>
        <div class="flex shrink-0 items-center gap-1">
          <button type="button" class="icon-btn size-9" title="修改名字" aria-label="修改名字" @click="startEditName"><Pencil class="size-4" /></button>
          <button type="button" class="icon-btn size-9" title="修改密码" aria-label="修改密码" aria-haspopup="dialog" @click="changingPassword = true"><KeyRound class="size-4" /></button>
        </div>
      </template>
    </div>

    <BaseModal v-if="changingPassword" title="修改密码" width="max-w-sm" @close="!savingPassword && closePassword()">
    <form class="space-y-3" data-testid="password-form" @submit.prevent="savePassword">
      <input v-model="current" type="password" class="field h-10 py-0" autocomplete="current-password" placeholder="当前密码" aria-label="当前密码" autofocus />
      <input v-model="next" type="password" class="field h-10 py-0" autocomplete="new-password" placeholder="新密码（至少 6 位）" aria-label="新密码" />
      <input v-model="again" type="password" class="field h-10 py-0" autocomplete="new-password" placeholder="再输一次新密码" aria-label="确认新密码" />
      <p v-if="passwordError" class="text-[13px] text-error" role="alert">{{ passwordError }}</p>
      <div class="flex justify-end gap-2 pt-2">
        <button type="button" class="btn-line" :disabled="savingPassword" @click="closePassword">取消</button>
        <button type="submit" class="btn-ink disabled:opacity-40" :disabled="!passwordReady || savingPassword">
          <LoaderCircle v-if="savingPassword" class="size-4 animate-spin" />确定
        </button>
      </div>
    </form>
    </BaseModal>
  </section>
</template>

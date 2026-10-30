<script setup lang="ts">
/** 登录 / 注册 */
import { ArrowRight, LoaderCircle } from '@lucide/vue'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { errorText } from '@/api/http'
import AuthStage from '@/components/brand/AuthStage.vue'
import LogoMark from '@/components/brand/LogoMark.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const mode = ref<'login' | 'register'>('login')
const account = ref('')
const password = ref('')
const displayName = ref('')
const loading = ref(false)
const error = ref('')
const hidingEyes = ref(false) // 输入密码时墨墨闭眼，不偷看

const title = computed(() => (mode.value === 'login' ? '欢迎回来' : '创建账号'))

async function submit() {
  error.value = ''
  if (!account.value.trim() || !password.value) {
    error.value = '请输入账号和密码'
    return
  }
  loading.value = true
  try {
    if (mode.value === 'login') await auth.login(account.value.trim(), password.value)
    else await auth.register(account.value.trim(), password.value, displayName.value.trim())
    const next = typeof route.query.next === 'string' ? route.query.next : '/'
    router.replace(auth.user?.onboarded ? next : { name: 'onboarding' })
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <AuthStage :mood="hidingEyes ? 'happy' : 'idle'">
    <div class="flex justify-center pt-4"><LogoMark :size="40" /></div>

    <div class="mx-auto mt-12 w-full max-w-[360px] flex-1">
      <h1 class="text-center text-[28px] font-semibold tracking-tight">{{ title }}</h1>
      <p class="mt-2 text-center text-sm text-mute">SheetMate · 会分析、会改表、会记住流程的 Excel 助手</p>

      <form class="mt-10 space-y-4" @submit.prevent="submit">
        <label class="block">
          <span class="mb-1.5 block text-xs font-medium text-mute">账号</span>
          <input v-model="account" class="field" autocomplete="username" placeholder="请输入账号" />
        </label>
        <label v-if="mode === 'register'" class="block">
          <span class="mb-1.5 block text-xs font-medium text-mute">昵称（可选）</span>
          <input v-model="displayName" class="field" placeholder="怎么称呼你" />
        </label>
        <label class="block">
          <span class="mb-1.5 block text-xs font-medium text-mute">密码</span>
          <input
            v-model="password"
            class="field"
            type="password"
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            placeholder="请输入密码"
            @focus="hidingEyes = true"
            @blur="hidingEyes = false"
          />
        </label>
        <p v-if="error" class="rounded-xl bg-error/5 px-3 py-2 text-sm text-error">{{ error }}</p>
        <button class="btn-ink w-full py-3" :disabled="loading">
          <LoaderCircle v-if="loading" class="size-4 animate-spin" />
          {{ mode === 'login' ? '登录' : '注册并登录' }}
          <ArrowRight v-if="!loading" class="size-4" />
        </button>
      </form>

      <p class="mt-6 text-center text-sm text-mute">
        <template v-if="mode === 'login'">
          还没有账号？<button class="font-medium text-ink underline-offset-4 hover:underline" @click="mode = 'register'">注册</button>
        </template>
        <template v-else>
          已经有账号？<button class="font-medium text-ink underline-offset-4 hover:underline" @click="mode = 'login'">去登录</button>
        </template>
      </p>
    </div>

    <p class="mt-8 text-center text-xs text-faint">本地部署 · 数据只存在你的电脑上 · 默认管理员 admin / admin123</p>
  </AuthStage>
</template>

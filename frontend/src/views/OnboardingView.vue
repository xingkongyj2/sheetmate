<script setup lang="ts">
/** 首次登录引导：三个问题（岗位 / 常处理的表 / 希望做什么），布局参考设计稿 */
import { ArrowLeft, ArrowRight } from '@lucide/vue'
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { errorText } from '@/api/http'
import AuthStage from '@/components/brand/AuthStage.vue'
import LogoMark from '@/components/brand/LogoMark.vue'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const toast = useToast()

interface Question {
  key: string
  title: string
  multiple: boolean
  options: string[]
  headline: string[]
}

const QUESTIONS: Question[] = [
  {
    key: 'job',
    title: '你的工作角色是？',
    multiple: false,
    options: ['财务 / 会计', '运营', '销售 / 客服', '数据分析', '物流 / 仓储', '人事 / 行政', '管理者', '其他'],
    headline: ['SHAPE YOUR', 'SHEETS WITH', 'SHEETMATE'],
  },
  {
    key: 'tables',
    title: '你最常处理哪些表格？',
    multiple: true,
    options: ['月结账单', '对账单', '销售报表', '库存表', '工资表', '订单明细', '费用报销', '其他'],
    headline: ['EVERY SHEET', 'HAS ITS', 'OWN RECIPE'],
  },
  {
    key: 'goals',
    title: '希望 SheetMate 帮你做什么？',
    multiple: true,
    options: ['统计分析', '整理改造表格', '每月重复处理', '多表匹配合并', '找出异常数据', '生成汇总报表'],
    headline: ['LEARN ONCE,', 'RUN EVERY', 'MONTH'],
  },
]

const step = ref(0)
const answers = ref<Record<string, string[]>>({ job: [], tables: [], goals: [] })
const saving = ref(false)
const question = computed(() => QUESTIONS[step.value])
const chosen = computed(() => answers.value[question.value.key])

function toggle(option: string) {
  const list = answers.value[question.value.key]
  if (question.value.multiple) {
    const index = list.indexOf(option)
    if (index >= 0) list.splice(index, 1)
    else list.push(option)
  } else {
    answers.value[question.value.key] = [option]
    setTimeout(next, 220)
  }
}

async function next() {
  if (step.value < QUESTIONS.length - 1) {
    step.value += 1
    return
  }
  await finish()
}

async function finish() {
  saving.value = true
  try {
    await auth.updateProfile({
      profile: { job: answers.value.job[0] ?? '', tables: answers.value.tables, goals: answers.value.goals, onboarded: true },
    })
    router.replace({ name: 'home' })
  } catch (err) {
    toast.error(errorText(err))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <AuthStage :headline="question.headline" :mood="step === 2 ? 'happy' : 'idle'">
    <div class="flex justify-center pt-4"><LogoMark :size="40" /></div>

    <div class="mx-auto mt-14 w-full max-w-[460px] flex-1">
      <Transition name="fade" mode="out-in">
        <div :key="step">
          <h1 class="text-center text-[26px] font-semibold tracking-tight">{{ question.title }}</h1>
          <p class="mt-2 text-center text-sm text-faint">{{ question.multiple ? '可以多选' : '选一个最接近的' }}</p>
          <div class="mt-12 grid grid-cols-2 gap-x-6 gap-y-3">
            <button
              v-for="option in question.options"
              :key="option"
              class="rounded-full px-4 py-3 text-[15px] transition"
              :class="chosen.includes(option) ? 'bg-ink text-white' : 'text-ink hover:bg-paper'"
              @click="toggle(option)"
            >
              {{ option }}
            </button>
          </div>
        </div>
      </Transition>

      <div v-if="question.multiple" class="mt-10 flex items-center justify-center gap-3">
        <button v-if="step > 0" class="btn-line" @click="step -= 1"><ArrowLeft class="size-4" />上一步</button>
        <button class="btn-ink" :disabled="saving" @click="next">
          {{ step === QUESTIONS.length - 1 ? '开始使用' : '下一步' }}<ArrowRight class="size-4" />
        </button>
      </div>
    </div>

    <div class="mt-8 flex items-center justify-between">
      <button class="text-xs text-faint hover:text-ink" @click="finish">跳过</button>
      <div class="flex gap-1.5">
        <span
          v-for="(_, i) in QUESTIONS"
          :key="i"
          class="h-1 rounded-full transition-all"
          :class="i === step ? 'w-6 bg-ink' : 'w-4 bg-line'"
        />
      </div>
      <span class="w-8" />
    </div>
  </AuthStage>
</template>

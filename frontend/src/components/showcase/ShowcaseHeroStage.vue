<script setup lang="ts">
/**
 * 宽幅真实工作台演示窗（1:1 按实际 SheetMate 界面还原，内置模拟数据与完整局部交互）：
 * 01 首页（新对话） · 02 对话与工作区面板（5 标签可切换） · 03 客户库 · 04 客户详情（4 标签可切换） · 05 流程与复跑
 */
import {
  ArrowLeft,
  ArrowUp,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleUserRound,
  Download,
  Eye,
  Maximize2,
  MessageSquarePlus,
  Play,
  Plus,
  Search,
  Sheet,
  ShieldCheck,
  Sparkles,
  SquarePen,
  X,
} from '@lucide/vue'
import { computed, onBeforeUnmount, ref } from 'vue'

import CustomerDetailTabIcon from '@/components/brand/CustomerDetailTabIcon.vue'
import LogoMark from '@/components/brand/LogoMark.vue'
import Mascot from '@/components/brand/Mascot.vue'
import UnboundCustomerIcon from '@/components/brand/UnboundCustomerIcon.vue'

type SceneId = 'home' | 'chat' | 'customers' | 'customer' | 'recipe'
type PanelTab = 'recipes' | 'files' | 'data' | 'usage' | 'privacy'
type CustomerTab = 'logic' | 'recipes' | 'files' | 'memory'

const SCENES: { id: SceneId; num: string; label: string }[] = [
  { id: 'home', num: '01', label: '首页' },
  { id: 'chat', num: '02', label: '对话与工作区' },
  { id: 'customers', num: '03', label: '客户库' },
  { id: 'customer', num: '04', label: '客户详情' },
  { id: 'recipe', num: '05', label: '流程与复跑' },
]

interface DemoCustomer {
  id: string
  name: string
  code: string
  mascot: string
  excerpt: string
  recipes: number
  files: number
  conversations: number
  lastActive: string
  prompt: string
  memories: string[]
  mappings: { std: string; aliases: string[] }[]
}

const CUSTOMERS: DemoCustomer[] = [
  {
    id: 'shunda',
    name: '顺达快递',
    code: 'SD-001',
    mascot: 'blob',
    excerpt: '按华东/华南/华北三区汇总运单明细，剔除退回件并套用 3kg 阶梯续重计费，自动核平总账。',
    recipes: 2,
    files: 7,
    conversations: 5,
    lastActive: '2 小时前',
    prompt: '按顺达 10 月口径，把 3 张区域运费表核对汇总成最终结算单',
    memories: [
      '退回件与作废单号不计入本月结算，需单独输出异常清单',
      '首重 3kg 内固定 ¥8.00，超出部分按每公斤 ¥2.50 阶梯计费',
      '总账结算金额保留两位小数，采用四舍五入口径',
    ],
    mappings: [
      { std: '运单编号', aliases: ['快递单号', '单号', 'Waybill'] },
      { std: '计费重量', aliases: ['计费重(kg)', '结算重量'] },
      { std: '结算金额', aliases: ['实收费用', '应付合计'] },
      { std: '发件网点', aliases: ['始发仓', '揽收网点'] },
    ],
  },
  {
    id: 'changxin',
    name: '安徽长鑫',
    code: 'CX-014',
    mascot: 'indigo-nerd',
    excerpt: '合并月度入库单与退料明细，按料号匹配协议单价，自动标记价差超过 2% 的异常行。',
    recipes: 3,
    files: 12,
    conversations: 8,
    lastActive: '昨天',
    prompt: '合并本月入库单与退料明细，标出单价差异大于 2% 的料号',
    memories: [
      '同一料号多批次入库需按加权平均单价核算',
      '退料数量以负值冲减本月应付总额',
    ],
    mappings: [
      { std: '物料编码', aliases: ['料号', 'SKU', '品号'] },
      { std: '协议单价', aliases: ['含税单价', '合同价'] },
      { std: '入库数量', aliases: ['实收数', '入库件数'] },
    ],
  },
  {
    id: 'xiaomi',
    name: '武汉小米',
    code: 'XM-008',
    mascot: 'party-wink',
    excerpt: '华中仓干线与城配账单月结，自动关联车型运费表并区分正常结算与超时扣款。',
    recipes: 2,
    files: 9,
    conversations: 4,
    lastActive: '3 天前',
    prompt: '核算华中仓 10 月干线运费，拆出正常结算与超时扣款明细',
    memories: [
      '超时超过 4 小时按当趟运费 5% 计提扣款',
      '节假日补贴按线路系数 1.15 倍自动上浮',
    ],
    mappings: [
      { std: '车次单号', aliases: ['发车单', '派车号'] },
      { std: '车型规格', aliases: ['车辆长度', '厢型'] },
      { std: '干线运费', aliases: ['基本运费', '协议价'] },
    ],
  },
  {
    id: 'xingchen',
    name: '星辰冷链',
    code: 'XC-022',
    mascot: 'sage-leaf',
    excerpt: '冷链温控附加费与里程补贴自动对账，多仓数据统一换算后输出财务复核底稿。',
    recipes: 1,
    files: 5,
    conversations: 3,
    lastActive: '4 天前',
    prompt: '核算星辰冷链本月里程补贴与温控附加费，输出财务复核表',
    memories: [
      '全程 -18℃ 冷冻线路固定叠加 12% 温控附加费',
      '单趟里程不足 50km 按起步里程结算',
    ],
    mappings: [
      { std: '线路名称', aliases: ['起止站点', '配送路线'] },
      { std: '温控等级', aliases: ['温层', '冷链类型'] },
      { std: '总里程', aliases: ['结算公里数', '里程(km)'] },
    ],
  },
]

const activeScene = ref<SceneId>('home')
/** 首页默认未绑定客户（干净空首页），点开下方抽屉可模拟选择客户 */
const homeCustomerId = ref<string | null>(null)
const pickerOpen = ref(false)
const detailCustomerId = ref<string>('shunda')
const panelTab = ref<PanelTab>('files')
const customerTab = ref<CustomerTab>('memory')
const privacyMasked = ref(true)
const flowStep = ref(4)

let flowTimer: number | null = null

const homeCustomer = computed(() => CUSTOMERS.find((c) => c.id === homeCustomerId.value) ?? null)
const currentCustomer = computed(() => CUSTOMERS.find((c) => c.id === detailCustomerId.value) ?? CUSTOMERS[0]!)

function goScene(id: SceneId) {
  pickerOpen.value = false
  activeScene.value = id
  if (id === 'recipe') runFlowDemo()
}

function openCustomerDetail(id: string) {
  pickerOpen.value = false
  detailCustomerId.value = id
  activeScene.value = 'customer'
}

function chooseHomeCustomer(id: string | null) {
  homeCustomerId.value = id
  pickerOpen.value = false
}

function runFlowDemo() {
  if (flowTimer) window.clearInterval(flowTimer)
  flowStep.value = 0
  flowTimer = window.setInterval(() => {
    if (flowStep.value >= 4) {
      if (flowTimer) window.clearInterval(flowTimer)
      flowTimer = null
      return
    }
    flowStep.value += 1
  }, 380)
}

onBeforeUnmount(() => {
  if (flowTimer) window.clearInterval(flowTimer)
})
</script>

<template>
  <div class="mx-auto flex w-full max-w-[1320px] flex-col">
    <!-- 实际软件壳层：自适应视口高度，保证卡片 + 下方 5 场景切换 Tab 完整收纳在单屏内 -->
    <div class="relative overflow-hidden rounded-[24px] border border-[#e3e1dc] bg-canvas shadow-[0_30px_90px_-34px_rgba(15,14,12,0.16),0_4px_18px_rgba(15,14,12,0.04)]">
      <div class="flex min-h-[500px] flex-col lg:h-[clamp(500px,calc(100svh-168px),780px)] lg:min-h-0 lg:flex-row">
        <!-- 左侧真实侧边栏（AppSidebar 1:1 复刻） -->
        <aside class="hidden w-[236px] shrink-0 flex-col justify-between p-3 lg:flex">
          <div class="min-h-0 flex-1 overflow-y-auto">
            <div class="flex h-11 items-center px-2">
              <LogoMark :size="22" with-text class="[&_span]:text-[17px]" />
            </div>

            <nav class="mt-1 space-y-0.5 text-[13px]">
              <div class="flex items-center gap-2.5 rounded-xl px-3 py-2 text-ink/75">
                <Search class="size-4 text-mute" />
                <span>搜索</span>
                <kbd class="ml-auto rounded-md border border-black/[0.08] px-1.5 font-sans text-[10.5px] text-faint">⌘K</kbd>
              </div>
              <button
                type="button"
                class="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left transition"
                :class="activeScene === 'home' ? 'sidebar-active' : 'text-ink/80 hover:bg-black/[0.04]'"
                @click="goScene('home')"
              >
                <SquarePen class="size-4" />
                <span>新对话</span>
              </button>
              <button
                type="button"
                class="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left transition"
                :class="activeScene === 'customers' || activeScene === 'customer' ? 'sidebar-active' : 'text-ink/80 hover:bg-black/[0.04]'"
                @click="goScene('customers')"
              >
                <CircleUserRound class="size-4" />
                <span>客户库</span>
                <span class="ml-auto text-xs text-faint">{{ CUSTOMERS.length }}</span>
              </button>
            </nav>

            <!-- 客户列表 -->
            <div class="mt-4 px-3 text-[11px] font-medium text-faint">客户</div>
            <div class="mt-1 space-y-0.5">
              <button
                v-for="cust in CUSTOMERS"
                :key="cust.id"
                type="button"
                class="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-1.5 text-left text-[13px] transition"
                :class="activeScene === 'customer' && detailCustomerId === cust.id ? 'sidebar-active' : 'text-ink/80 hover:bg-black/[0.04]'"
                @click="openCustomerDetail(cust.id)"
              >
                <Mascot :variant="cust.mascot" :size="20" :shadow="false" />
                <span class="truncate">{{ cust.name }}</span>
              </button>
            </div>

            <!-- 对话列表 -->
            <div class="mt-4 px-3 text-[11px] font-medium text-faint">对话</div>
            <div class="mt-1 space-y-0.5 text-[13px]">
              <button
                type="button"
                class="flex w-full items-center justify-between rounded-xl px-3 py-1.5 text-left transition"
                :class="activeScene === 'chat' ? 'sidebar-active' : 'text-ink/75 hover:bg-black/[0.04]'"
                @click="goScene('chat')"
              >
                <span class="truncate">10月区域运费核算</span>
              </button>
              <button
                type="button"
                class="flex w-full items-center justify-between rounded-xl px-3 py-1.5 text-left transition"
                :class="activeScene === 'recipe' ? 'sidebar-active' : 'text-ink/75 hover:bg-black/[0.04]'"
                @click="goScene('recipe')"
              >
                <span class="truncate">顺达月结流程 v3</span>
              </button>
              <button
                type="button"
                class="flex w-full items-center justify-between rounded-xl px-3 py-1.5 text-left text-ink/70 transition hover:bg-black/[0.04]"
                @click="goScene('chat'); panelTab = 'privacy'"
              >
                <span class="truncate">华东仓脱敏核对</span>
              </button>
            </div>
          </div>

          <div class="flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-ink/85">
            <span class="flex size-6 items-center justify-center rounded-full bg-ink text-[11px] font-medium text-white">结</span>
            <span class="truncate">结算专员</span>
          </div>
        </aside>

        <!-- 右侧主内容悬浮白板（AppShell 1:1 复刻） -->
        <div class="flex min-h-0 min-w-0 flex-1 p-2.5 lg:pl-0">
          <div class="app-panel flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[18px] bg-panel">
            <Transition name="scene-fade" mode="out-in">
              <!-- ========== 01 真实空首页（HomeView 1:1 复刻） ========== -->
              <div
                v-if="activeScene === 'home'"
                key="home"
                class="flex min-h-0 flex-1 flex-col justify-between overflow-y-auto px-6 pb-4 pt-8 sm:px-12"
              >
                <div class="mx-auto flex w-full max-w-[640px] flex-1 flex-col justify-center pb-4">
                  <div class="flex items-center justify-center gap-3 text-[24px] font-semibold tracking-tight text-ink sm:text-[28px]">
                    <LogoMark :size="30" />
                    <span>你的专属手工账单助手</span>
                  </div>

                  <!-- 干净空输入框 + 右上角探头墨墨 + 底部「选择客户」抽屉 -->
                  <div class="mt-12 w-full xl:mt-16">
                    <div class="relative z-10">
                      <div class="pointer-events-none absolute bottom-full right-6 aspect-[33/20] w-[144px] overflow-hidden">
                        <Transition name="peek-pop" mode="out-in">
                          <Mascot
                            :key="homeCustomer?.mascot ?? 'blob'"
                            :variant="homeCustomer?.mascot ?? 'blob'"
                            :size="144"
                            :shadow="false"
                            class="h-auto w-full"
                          />
                        </Transition>
                      </div>

                      <div class="chat-card p-4">
                        <p class="min-h-[58px] text-[15px] leading-relaxed text-faint">
                          账单的一切，交给我
                        </p>

                        <div class="mt-3 flex items-center justify-between">
                          <div class="flex items-center gap-2">
                            <span class="inline-flex size-8 items-center justify-center rounded-full text-mute">
                              <Plus class="size-4" />
                            </span>
                            <span class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs text-mute">
                              <Sparkles class="size-3.5" />
                              思考模式
                            </span>
                          </div>
                          <span class="inline-flex size-8 items-center justify-center rounded-full bg-[#e9e7e2] text-[#a8a49e]">
                            <ArrowUp class="size-4" />
                          </span>
                        </div>
                      </div>
                    </div>

                    <!-- 真实「选择客户」抽屉（默认空状态，点开可切换客户） -->
                    <div class="relative mx-5 -mt-4 rounded-b-xl border border-t-0 border-[#eeeeed] bg-white pt-4 shadow-[0_1px_2px_rgba(0,0,0,0.04),0_6px_20px_rgba(0,0,0,0.07)]">
                      <div
                        class="flex h-10 w-full cursor-pointer items-center gap-1.5 rounded-b-xl px-3.5 text-[13px] text-mute transition hover:bg-black/[0.03] hover:text-ink"
                        @click="pickerOpen = !pickerOpen"
                      >
                        <span class="flex size-6 shrink-0 items-center justify-center overflow-hidden rounded-full bg-paper">
                          <UnboundCustomerIcon v-if="!homeCustomer" class="size-5 shrink-0" />
                          <Mascot v-else :variant="homeCustomer.mascot" :size="26" :shadow="false" />
                        </span>
                        <span class="truncate" :class="homeCustomer ? 'font-medium text-ink' : ''">
                          {{ homeCustomer ? homeCustomer.name : '选择客户' }}
                        </span>
                        <button
                          v-if="homeCustomer"
                          type="button"
                          class="flex size-5 items-center justify-center rounded-full text-faint hover:bg-black/[0.07] hover:text-ink"
                          title="取消选择"
                          @click.stop="chooseHomeCustomer(null)"
                        >
                          <X class="size-3" />
                        </button>
                        <ChevronDown class="ml-auto size-3.5 text-faint transition" :class="{ 'rotate-180': pickerOpen }" />
                      </div>

                      <!-- 点开后的轻巧客户选择列表 -->
                      <div
                        v-if="pickerOpen"
                        class="absolute inset-x-0 top-full z-20 mt-1.5 rounded-xl border border-line bg-white p-1.5 shadow-lg"
                      >
                        <button
                          v-for="cust in CUSTOMERS"
                          :key="cust.id"
                          type="button"
                          class="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] transition hover:bg-paper"
                          @click="chooseHomeCustomer(cust.id)"
                        >
                          <Mascot :variant="cust.mascot" :size="22" :shadow="false" />
                          <span class="flex-1 text-ink">{{ cust.name }}</span>
                          <Check v-if="homeCustomerId === cust.id" class="size-3.5 text-ink" />
                        </button>
                      </div>
                    </div>
                  </div>
                </div>

                <div class="text-center text-[11px] tracking-wide text-faint">本产品归属「数智结算工坊」</div>
              </div>

              <!-- ========== 02 真实对话 + 右侧工作区面板（ChatView + ExecutionPanel） ========== -->
              <div
                v-else-if="activeScene === 'chat'"
                key="chat"
                class="grid min-h-0 flex-1 divide-y divide-line/80 lg:grid-cols-[1.25fr_0.85fr] lg:divide-x lg:divide-y-0"
              >
                <!-- 左：对话主栏 -->
                <div class="flex min-h-0 flex-col">
                  <div class="flex h-[54px] shrink-0 items-center justify-between border-b border-line/70 px-5">
                    <span class="text-[14px] font-medium text-ink">10月区域运费核算</span>
                    <button
                      type="button"
                      class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs text-mute transition hover:bg-paper hover:text-ink"
                      @click="openCustomerDetail('shunda')"
                    >
                      <Mascot variant="blob" :size="18" :shadow="false" />
                      <span>顺达快递</span>
                    </button>
                  </div>

                  <div class="flex-1 space-y-5 overflow-y-auto p-6">
                    <!-- 用户消息 -->
                    <div class="ml-auto max-w-[82%] space-y-1.5">
                      <div class="flex flex-wrap justify-end gap-1.5">
                        <span class="inline-flex items-center gap-1 rounded-lg border border-line bg-white px-2 py-0.5 text-[11px] text-mute">
                          <Sheet class="size-3 text-[#22b573]" /> 华东运单明细.xlsx
                        </span>
                        <span class="inline-flex items-center gap-1 rounded-lg border border-line bg-white px-2 py-0.5 text-[11px] text-mute">
                          <Sheet class="size-3 text-[#22b573]" /> 华南运单明细.xlsx
                        </span>
                        <span class="inline-flex items-center gap-1 rounded-lg border border-line bg-white px-2 py-0.5 text-[11px] text-mute">
                          <Sheet class="size-3 text-[#22b573]" /> 阶梯价目表.xlsx
                        </span>
                      </div>
                      <div class="rounded-2xl rounded-tr-sm bg-ink px-4 py-2.5 text-[13.5px] leading-relaxed text-white">
                        按顺达 10 月口径，把 3 张区域运费表核对汇总成最终结算单。
                      </div>
                    </div>

                    <!-- 助手回复 -->
                    <div class="flex items-start gap-3">
                      <Mascot variant="blob" :size="30" :shadow="false" class="mt-0.5 shrink-0" />
                      <div class="min-w-0 flex-1 space-y-3.5">
                        <div class="flex flex-wrap gap-2 text-xs text-mute">
                          <span class="inline-flex items-center gap-1 rounded-full bg-paper px-2.5 py-1">
                            <CheckCircle2 class="size-3.5 text-[#22b573]" /> 读取 1,842 条运单
                          </span>
                          <span class="inline-flex items-center gap-1 rounded-full bg-paper px-2.5 py-1">
                            <CheckCircle2 class="size-3.5 text-[#22b573]" /> 对齐 14 组字段别名
                          </span>
                          <span class="inline-flex items-center gap-1 rounded-full bg-paper px-2.5 py-1">
                            <CheckCircle2 class="size-3.5 text-[#22b573]" /> 剔除 23 条退回件
                          </span>
                        </div>

                        <p class="text-[13.5px] leading-relaxed text-ink">已完成 10 月三区运费核算汇总，成品账单与异常清单均已生成：</p>

                        <!-- 真实 Markdown 汇总表样式 -->
                        <div class="overflow-hidden rounded-xl border border-[#dfe3e8] bg-white">
                          <table class="w-full text-left text-[12.5px] tabular-nums">
                            <thead class="border-b border-[#dfe3e8] bg-[#f7f8fa] text-xs text-mute">
                              <tr>
                                <th class="px-3.5 py-2.5 font-semibold">结算区域</th>
                                <th class="px-3.5 py-2.5 font-semibold">有效单量</th>
                                <th class="px-3.5 py-2.5 font-semibold text-right">结算金额</th>
                                <th class="px-3.5 py-2.5 font-semibold text-right">状态</th>
                              </tr>
                            </thead>
                            <tbody class="divide-y divide-[#eef0f3]">
                              <tr>
                                <td class="px-3.5 py-2.5 font-medium">华东仓</td>
                                <td class="px-3.5 py-2.5 text-mute">812</td>
                                <td class="px-3.5 py-2.5 text-right font-mono">¥ 48,920.00</td>
                                <td class="px-3.5 py-2.5 text-right text-[#1fa463]">正常</td>
                              </tr>
                              <tr class="bg-[#fafbfc]">
                                <td class="px-3.5 py-2.5 font-medium">华南仓</td>
                                <td class="px-3.5 py-2.5 text-mute">645</td>
                                <td class="px-3.5 py-2.5 text-right font-mono">¥ 36,415.50</td>
                                <td class="px-3.5 py-2.5 text-right text-[#1fa463]">正常</td>
                              </tr>
                              <tr>
                                <td class="px-3.5 py-2.5 font-medium">华北仓</td>
                                <td class="px-3.5 py-2.5 text-mute">362</td>
                                <td class="px-3.5 py-2.5 text-right font-mono">¥ 21,084.50</td>
                                <td class="px-3.5 py-2.5 text-right text-[#1fa463]">正常</td>
                              </tr>
                              <tr class="bg-[#f7f8fa] font-semibold">
                                <td class="px-3.5 py-2.5">合计</td>
                                <td class="px-3.5 py-2.5">1,819</td>
                                <td class="px-3.5 py-2.5 text-right font-mono">¥ 106,420.00</td>
                                <td class="px-3.5 py-2.5 text-right text-[#1fa463]">已生成</td>
                              </tr>
                            </tbody>
                          </table>
                        </div>

                        <!-- 生成的可下载成品卡 -->
                        <div class="flex items-center justify-between rounded-xl border border-line/80 bg-white px-3.5 py-2.5">
                          <div class="flex min-w-0 items-center gap-2.5">
                            <Sheet class="size-4 shrink-0 text-[#1fa463]" />
                            <span class="truncate text-[13px] font-medium text-ink">顺达_10月结算汇总单.xlsx</span>
                          </div>
                          <span class="shrink-0 rounded-full bg-paper px-2.5 py-0.5 text-[11px] text-mute">点击右侧面板查看</span>
                        </div>
                      </div>
                    </div>
                  </div>

                  <!-- 真实底部对话输入框 -->
                  <div class="shrink-0 px-6 pb-5 pt-2">
                    <div class="chat-card px-4 py-3">
                      <div class="text-[13.5px] text-faint">继续提问...</div>
                      <div class="mt-3 flex items-center justify-between">
                        <div class="flex items-center gap-2 text-xs text-mute">
                          <Plus class="size-4" />
                          <span class="inline-flex items-center gap-1"><Sparkles class="size-3.5" /> 思考模式</span>
                        </div>
                        <span class="inline-flex size-7 items-center justify-center rounded-full bg-[#e9e7e2] text-[#a8a49e]">
                          <ArrowUp class="size-3.5" />
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- 右：真实 ExecutionPanel（5 个标签均可点击切换！） -->
                <div class="flex min-h-0 flex-col bg-[#fdfdfc]">
                  <div class="flex h-[52px] shrink-0 items-center px-4">
                    <span class="text-[15px] font-semibold tracking-tight text-ink">工作区面板</span>
                  </div>
                  <div class="shrink-0 px-4 pb-3">
                    <div class="grid grid-cols-5 gap-0.5 rounded-xl bg-paper p-1 text-xs">
                      <button
                        v-for="t in ([
                          { key: 'recipes', label: '流程' },
                          { key: 'files', label: '文件' },
                          { key: 'data', label: '数据' },
                          { key: 'usage', label: '消耗' },
                          { key: 'privacy', label: '脱敏' },
                        ] as const)"
                        :key="t.key"
                        type="button"
                        class="rounded-lg py-1.5 text-center transition"
                        :class="panelTab === t.key ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
                        @click="panelTab = t.key"
                      >
                        {{ t.label }}
                      </button>
                    </div>
                  </div>

                  <div class="min-h-0 flex-1 overflow-y-auto px-4 pb-5 pt-1">
                    <!-- 文件 -->
                    <div v-if="panelTab === 'files'" class="space-y-4">
                      <div>
                        <div class="label-mini mb-2">结果文件 <span class="font-normal">1</span></div>
                        <div class="flex items-center gap-2.5 rounded-xl border border-line/80 bg-white px-3 py-2.5">
                          <Sheet class="size-4 shrink-0 text-ink" />
                          <span class="flex-1 truncate text-[13px] font-medium">顺达_10月结算汇总单.xlsx</span>
                          <Download class="size-4 text-mute" />
                        </div>
                      </div>
                      <div>
                        <div class="label-mini mb-2">输入文件 <span class="font-normal">3</span></div>
                        <div class="space-y-1.5">
                          <div
                            v-for="f in ['华东运单明细.xlsx', '华南运单明细.xlsx', '阶梯价目表.xlsx']"
                            :key="f"
                            class="flex items-center gap-2.5 rounded-xl border border-line/80 bg-white px-3 py-2"
                          >
                            <Sheet class="size-4 shrink-0 text-mute" />
                            <span class="flex-1 truncate text-[13px]">{{ f }}</span>
                            <ShieldCheck class="size-3.5 text-[#1fa463]" />
                          </div>
                        </div>
                      </div>
                    </div>

                    <!-- 流程 -->
                    <div v-else-if="panelTab === 'recipes'" class="space-y-2">
                      <div
                        class="flex cursor-pointer items-center gap-2 rounded-xl border border-line/80 bg-white px-3.5 py-3 transition hover:border-ink/25"
                        @click="goScene('recipe')"
                      >
                        <div class="min-w-0 flex-1">
                          <div class="truncate text-sm font-medium text-ink">顺达月结标准流程</div>
                          <div class="mt-0.5 text-xs text-faint">v3 · 4 个节点 · 已沉淀</div>
                        </div>
                        <Maximize2 class="size-3.5 text-mute" />
                      </div>
                    </div>

                    <!-- 数据 -->
                    <div v-else-if="panelTab === 'data'" class="space-y-2">
                      <div class="label-mini mb-2">中间数据 <span class="font-normal">2</span></div>
                      <div
                        v-for="tb in [
                          { name: 'waybills_cleaned', size: '1819×12', src: '华东/华南/华北合并' },
                          { name: 'monthly_settlement', size: '4×6', src: '区域汇总核验结果' },
                        ]"
                        :key="tb.name"
                        class="rounded-xl border border-line/80 bg-white px-3 py-2.5"
                      >
                        <div class="flex items-center gap-2">
                          <Sheet class="size-4 text-mute" />
                          <span class="flex-1 truncate font-mono text-[13px] font-medium">{{ tb.name }}</span>
                          <span class="font-mono text-[11px] text-faint">{{ tb.size }}</span>
                        </div>
                        <div class="mt-1 flex items-center justify-between text-[11px] text-faint">
                          <span>{{ tb.src }}</span>
                          <span class="inline-flex items-center gap-1 text-mute"><Eye class="size-3" /> 查看</span>
                        </div>
                      </div>
                    </div>

                    <!-- 消耗 -->
                    <div v-else-if="panelTab === 'usage'" class="space-y-2.5">
                      <div class="grid grid-cols-2 gap-2.5">
                        <div class="rounded-xl border border-line/80 bg-white p-3">
                          <div class="text-xs text-faint">输入 Token</div>
                          <div class="mt-1 font-mono text-lg font-semibold text-ink">4,280</div>
                        </div>
                        <div class="rounded-xl border border-line/80 bg-white p-3">
                          <div class="text-xs text-faint">输出 Token</div>
                          <div class="mt-1 font-mono text-lg font-semibold text-ink">1,640</div>
                        </div>
                      </div>
                      <div class="rounded-xl border border-line/80 bg-white p-3 text-xs text-mute">
                        总耗时 <span class="font-mono font-medium text-ink">11.4s</span> · 流程沉淀后复跑不消耗模型 Token
                      </div>
                    </div>

                    <!-- 脱敏 -->
                    <div v-else class="space-y-3">
                      <div class="flex items-center justify-between rounded-xl bg-[#effaf4] px-3 py-2.5 text-xs text-[#158752]">
                        <span class="inline-flex items-center gap-1.5 font-medium"><ShieldCheck class="size-4" /> 已拦截 3 类敏感列</span>
                        <button
                          type="button"
                          class="rounded-full bg-white px-2.5 py-0.5 text-[11px] font-medium text-ink shadow-2xs"
                          @click="privacyMasked = !privacyMasked"
                        >
                          {{ privacyMasked ? '查看原文' : '查看脱敏' }}
                        </button>
                      </div>
                      <div class="space-y-1.5 text-xs">
                        <div
                          v-for="item in [
                            { col: '收件人', masked: '[NAME_01]', raw: '张明远' },
                            { col: '联系电话', masked: '[PHONE_01]', raw: '138-6612-8821' },
                            { col: '客户网点', masked: '[ORG_01]', raw: '顺达华东枢纽' },
                          ]"
                          :key="item.col"
                          class="flex items-center justify-between rounded-xl border border-line/80 bg-white px-3 py-2.5"
                        >
                          <span class="text-mute">{{ item.col }}</span>
                          <span class="font-mono" :class="privacyMasked ? 'text-[#5255c8]' : 'text-ink'">
                            {{ privacyMasked ? item.masked : item.raw }}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- ========== 03 真实客户库（CustomersView 1:1 复刻） ========== -->
              <div
                v-else-if="activeScene === 'customers'"
                key="customers"
                class="flex min-h-0 flex-1 flex-col overflow-y-auto px-6 py-7 sm:px-8"
              >
                <div class="flex items-center gap-3">
                  <h3 class="text-xl font-semibold tracking-tight text-ink">客户库</h3>
                  <div class="ml-auto flex items-center gap-2">
                    <div class="flex h-8 w-44 items-center gap-2 rounded-full bg-paper px-3 text-xs text-faint">
                      <Search class="size-3.5" />
                      <span>搜索客户</span>
                    </div>
                    <span class="btn-ink size-8 !p-0"><Plus class="size-4" /></span>
                  </div>
                </div>

                <div class="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                  <button
                    v-for="c in CUSTOMERS"
                    :key="c.id"
                    type="button"
                    class="group flex flex-col overflow-hidden rounded-[22px] bg-white text-left shadow-[0_4px_24px_rgba(0,0,0,0.06),0_1px_4px_rgba(0,0,0,0.03)] transition duration-300 hover:-translate-y-0.5 hover:shadow-[0_8px_28px_rgba(0,0,0,0.09)]"
                    @click="openCustomerDetail(c.id)"
                  >
                    <div class="relative h-24 overflow-hidden bg-paper">
                      <div class="absolute -bottom-8 left-1/2 -translate-x-1/2 transition-transform duration-300 group-hover:-translate-y-2">
                        <Mascot :variant="c.mascot" :size="108" :shadow="false" />
                      </div>
                    </div>
                    <div class="flex flex-1 flex-col p-4">
                      <div class="flex items-baseline gap-2">
                        <span class="truncate text-base font-semibold text-ink">{{ c.name }}</span>
                        <span class="font-mono text-[11px] text-faint">{{ c.code }}</span>
                      </div>
                      <p class="mt-1.5 line-clamp-2 text-[12.5px] leading-relaxed text-mute">{{ c.excerpt }}</p>
                      <div class="mt-auto flex items-center gap-2.5 pt-4 text-[11.5px] text-faint">
                        <span><b class="font-semibold text-ink">{{ c.recipes }}</b> 流程</span>
                        <span><b class="font-semibold text-ink">{{ c.files }}</b> 文件</span>
                        <span><b class="font-semibold text-ink">{{ c.conversations }}</b> 对话</span>
                        <span class="ml-auto">{{ c.lastActive }}</span>
                      </div>
                    </div>
                  </button>
                </div>
              </div>

              <!-- ========== 04 真实客户详情（CustomerDetailView 1:1 复刻） ========== -->
              <div
                v-else-if="activeScene === 'customer'"
                key="customer"
                class="flex min-h-0 flex-1 flex-col overflow-y-auto px-6 py-6 sm:px-10"
              >
                <div class="mx-auto w-full max-w-[740px]">
                  <button type="button" class="btn-ink size-8 !p-0" title="返回客户库" @click="goScene('customers')">
                    <ArrowLeft class="size-3.5" />
                  </button>

                  <div class="mt-4 flex flex-wrap items-center gap-4">
                    <div class="flex size-20 items-center justify-center overflow-hidden rounded-[24px] bg-paper">
                      <Mascot :variant="currentCustomer.mascot" :size="82" :shadow="false" />
                    </div>
                    <div class="min-w-0 flex-1">
                      <div class="flex items-baseline gap-2.5">
                        <h3 class="text-2xl font-semibold tracking-tight text-ink">{{ currentCustomer.name }}</h3>
                        <span class="font-mono text-xs text-faint">{{ currentCustomer.code }}</span>
                      </div>
                      <div class="mt-1.5 flex items-center gap-4 text-xs text-mute">
                        <span><b class="font-medium text-ink">{{ currentCustomer.conversations }}</b> 个对话</span>
                        <span>最近活动 {{ currentCustomer.lastActive }}</span>
                      </div>
                      <button type="button" class="btn-ink mt-2.5 h-8 gap-1.5 px-3 py-0 text-xs" @click="goScene('home')">
                        <MessageSquarePlus class="size-3.5" /> 新建对话
                      </button>
                    </div>
                  </div>

                  <!-- 真实胶囊标签栏（4 标签均可点击） -->
                  <div class="mt-6 flex w-fit items-center gap-1 rounded-full bg-paper/80 p-1.5">
                    <button
                      v-for="t in ([
                        { key: 'logic', label: '处理逻辑' },
                        { key: 'recipes', label: '流程', count: currentCustomer.recipes },
                        { key: 'files', label: '文件', count: currentCustomer.files },
                        { key: 'memory', label: '记忆与字段' },
                      ] as const)"
                      :key="t.key"
                      type="button"
                      class="inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] transition"
                      :class="customerTab === t.key ? 'bg-white font-medium text-ink shadow-xs' : 'text-mute hover:text-ink'"
                      @click="customerTab = t.key"
                    >
                      <CustomerDetailTabIcon :name="t.key" class="size-4" />
                      <span>{{ t.label }}</span>
                      <span v-if="'count' in t" class="rounded-full bg-black/[0.05] px-1.5 py-0.5 text-[10.5px] leading-none text-mute">{{ t.count }}</span>
                    </button>
                  </div>

                  <!-- 记忆与字段（真实双卡片） -->
                  <div v-if="customerTab === 'memory'" class="mt-5 grid gap-4 sm:grid-cols-2">
                    <div class="rounded-[22px] bg-white p-5 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.08),0_0_0_1px_rgba(34,34,66,0.04)]">
                      <div class="text-sm font-semibold text-ink">记忆</div>
                      <ul class="mt-3 space-y-2 text-[12.5px] leading-relaxed text-ink/85">
                        <li v-for="m in currentCustomer.memories" :key="m" class="rounded-xl bg-[#faf9f7] px-3 py-2">{{ m }}</li>
                      </ul>
                    </div>
                    <div class="rounded-[22px] bg-white p-5 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.08),0_0_0_1px_rgba(34,34,66,0.04)]">
                      <div class="text-sm font-semibold text-ink">字段映射</div>
                      <div class="mt-3 divide-y divide-line/60 text-[12.5px]">
                        <div v-for="row in currentCustomer.mappings" :key="row.std" class="flex items-center justify-between gap-2 py-2">
                          <span class="font-medium text-ink">{{ row.std }}</span>
                          <span class="text-faint">→</span>
                          <span class="truncate text-mute">{{ row.aliases.join('、') }}</span>
                        </div>
                      </div>
                    </div>
                  </div>

                  <!-- 处理逻辑 -->
                  <div v-else-if="customerTab === 'logic'" class="mt-5 rounded-[22px] bg-white p-6 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.08),0_0_0_1px_rgba(34,34,66,0.04)]">
                    <div class="text-sm font-semibold text-ink">{{ currentCustomer.name }} · 主结算逻辑</div>
                    <p class="mt-2.5 text-[13.5px] leading-7 text-mute">{{ currentCustomer.excerpt }}</p>
                  </div>

                  <!-- 流程 -->
                  <div v-else-if="customerTab === 'recipes'" class="mt-5 space-y-3">
                    <div class="flex items-center justify-between rounded-[20px] bg-white px-5 py-4 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.08),0_0_0_1px_rgba(34,34,66,0.04)]">
                      <div>
                        <div class="text-sm font-semibold text-ink">{{ currentCustomer.name }}月结标准流程</div>
                        <div class="mt-0.5 text-xs text-faint">v3 · 最近运行 2 小时前 · 核验通过</div>
                      </div>
                      <button type="button" class="btn-ink h-8 px-3.5 py-0 text-xs" @click="goScene('recipe')">查看流程</button>
                    </div>
                  </div>

                  <!-- 文件 -->
                  <div v-else class="mt-5 rounded-[22px] bg-white p-5 shadow-[0_4px_24px_-8px_rgba(34,34,66,0.08),0_0_0_1px_rgba(34,34,66,0.04)]">
                    <div class="space-y-2 text-[13px]">
                      <div v-for="fn in ['华东运单明细.xlsx', '阶梯价目表.xlsx', '10月结算汇总单.xlsx']" :key="fn" class="flex items-center gap-2.5 rounded-xl bg-[#faf9f7] px-3.5 py-2.5">
                        <Sheet class="size-4 text-mute" />
                        <span class="flex-1">{{ fn }}</span>
                        <span class="text-xs text-faint">已归档</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- ========== 05 真实流程与复跑（RecipeDetailView 1:1 复刻） ========== -->
              <div
                v-else
                key="recipe"
                class="flex min-h-0 flex-1 flex-col justify-between overflow-y-auto px-6 py-6 sm:px-10"
              >
                <div class="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div class="flex items-center gap-2">
                      <h3 class="text-xl font-semibold tracking-tight text-ink">顺达月结标准流程</h3>
                      <span class="rounded-full bg-paper px-2.5 py-0.5 font-mono text-xs text-mute">v3</span>
                    </div>
                    <div class="mt-1 text-xs text-faint">4 个处理节点 · 核验规则已开启 · 最近复跑耗时 12 秒</div>
                  </div>

                  <button
                    type="button"
                    class="btn-ink h-9 gap-1.5 px-4 py-0 text-xs"
                    @click="runFlowDemo"
                  >
                    <Play class="size-3.5 fill-current" />
                    <span>执行流程</span>
                  </button>
                </div>

                <!-- 流程图画布 -->
                <div class="my-6 flex flex-1 items-center justify-center rounded-2xl border border-line/80 bg-[#faf9f7] px-6 py-10">
                  <div class="relative grid w-full max-w-[820px] grid-cols-2 gap-4 lg:grid-cols-4">
                    <svg class="pointer-events-none absolute inset-x-12 top-1/2 hidden h-4 -translate-y-1/2 lg:block" viewBox="0 0 680 16" fill="none">
                      <line x1="0" y1="8" x2="680" y2="8" stroke="#c8cbf5" stroke-width="2" class="flow-dash" />
                    </svg>

                    <div
                      v-for="node in [
                        { step: 1, title: '读入多表', detail: '3 份原始明细', dot: '#22b573' },
                        { step: 2, title: '字段对齐', detail: '套用 14 组别名', dot: '#6265dc' },
                        { step: 3, title: '阶梯计费', detail: '公式库自动核算', dot: '#ff7a1a' },
                        { step: 4, title: '输出核平', detail: '交付汇总+异常表', dot: '#22b573' },
                      ]"
                      :key="node.step"
                      class="relative z-10 rounded-2xl border bg-white p-4 transition duration-300"
                      :class="flowStep >= node.step ? 'border-[#cfd2f8] shadow-[0_8px_22px_-10px_rgba(98,101,220,0.28)]' : 'border-line opacity-55'"
                    >
                      <div class="flex items-center justify-between">
                        <span class="size-2.5 rounded-full" :style="{ backgroundColor: node.dot }" />
                        <span
                          class="inline-flex size-5 items-center justify-center rounded-full transition"
                          :class="flowStep >= node.step ? 'bg-[#e8f8ef] text-[#158752]' : 'bg-paper text-faint'"
                        >
                          <Check class="size-3" />
                        </span>
                      </div>
                      <div class="mt-3 text-sm font-semibold text-ink">{{ node.title }}</div>
                      <div class="mt-1 text-xs text-mute">{{ node.detail }}</div>
                    </div>
                  </div>
                </div>

                <!-- 底部核验与小团子 -->
                <div class="flex flex-wrap items-center justify-between gap-3 border-t border-line/70 pt-4 text-xs text-mute">
                  <div class="flex items-center gap-2.5">
                    <Mascot variant="ink-party" :size="28" :shadow="false" />
                    <span>总账差异为 0，已生成「顺达_10月结算汇总单.xlsx」</span>
                  </div>
                  <span class="rounded-full bg-[#e9f9f0] px-2.5 py-1 font-medium text-[#158752]">核验通过</span>
                </div>
              </div>
            </Transition>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部极简 5 场景切换条（默认停在 01 首页，点击自由体验其他页面） -->
    <div class="mx-auto mt-3.5 w-full max-w-[780px] shrink-0">
      <div class="grid grid-cols-2 gap-1.5 rounded-2xl border border-[#e6e4df] bg-white p-1.5 shadow-[0_6px_22px_-12px_rgba(15,14,12,0.1)] sm:grid-cols-5">
        <button
          v-for="scene in SCENES"
          :key="scene.id"
          type="button"
          class="relative rounded-xl px-3 py-2 text-left transition"
          :class="activeScene === scene.id ? 'bg-[#f4f3ef] text-ink' : 'text-mute hover:bg-[#faf9f7] hover:text-ink'"
          @click="goScene(scene.id)"
        >
          <div class="flex items-center gap-1.5">
            <span class="font-mono text-[10.5px]" :class="activeScene === scene.id ? 'font-semibold text-[#6265dc]' : 'text-faint'">{{ scene.num }}</span>
            <span class="truncate text-[13px] font-medium">{{ scene.label }}</span>
          </div>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.scene-fade-enter-active,
.scene-fade-leave-active {
  transition: opacity 0.24s cubic-bezier(0.22, 1, 0.36, 1), transform 0.24s cubic-bezier(0.22, 1, 0.36, 1);
}
.scene-fade-enter-from {
  opacity: 0;
  transform: translateY(6px);
}
.scene-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

.peek-pop-enter-active,
.peek-pop-leave-active {
  transition: opacity 0.2s ease, transform 0.26s cubic-bezier(0.2, 0.9, 0.3, 1.25);
}
.peek-pop-enter-from {
  opacity: 0;
  transform: translateY(14px) scale(0.92);
}
.peek-pop-leave-to {
  opacity: 0;
  transform: translateY(10px) scale(0.94);
}
</style>

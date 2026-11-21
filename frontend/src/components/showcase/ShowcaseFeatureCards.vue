<script setup lang="ts">
/**
 * 简约核心功能卡（6 项重点能力，3 列白卡）：
 * - 每张卡只保留一个干净的视觉焦点 + 小团子点缀 + 标题 + 一句话
 */
import { ArrowRight, Check, ShieldCheck } from '@lucide/vue'
import { computed, ref } from 'vue'

import Mascot from '@/components/brand/Mascot.vue'

const masked = ref(true)
const weight = ref(6)
const price = computed(() => (weight.value > 3 ? 8 + (weight.value - 3) * 2.5 : 8).toFixed(2))
</script>

<template>
  <div class="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
    <!-- 1. 对话处理表格 -->
    <article class="reveal-item group rounded-[22px] border border-[#eae8e2] bg-white p-4 transition duration-300 hover:-translate-y-0.5 hover:shadow-[0_14px_34px_-22px_rgba(15,14,12,0.16)]">
      <div class="flex h-[156px] flex-col justify-between rounded-2xl bg-[#f6f5f2] p-4">
        <div class="ml-auto rounded-2xl rounded-tr-sm bg-ink px-3 py-1.5 text-[11.5px] text-white">
          核对三区运费并输出汇总单
        </div>
        <div class="flex items-end gap-2.5">
          <Mascot variant="blob" :size="46" :shadow="false" class="shrink-0 transition-transform duration-300 group-hover:-translate-y-1" />
          <div class="flex-1 rounded-xl border border-[#e6e4df] bg-white px-3 py-2 text-[11.5px]">
            <div class="flex items-center justify-between">
              <span class="font-medium text-ink">10月结算单.xlsx</span>
              <span class="inline-flex size-4 items-center justify-center rounded-full bg-[#e9f9f0] text-[#158752]"><Check class="size-2.5" /></span>
            </div>
            <div class="mt-1 font-mono text-[11px] text-mute">¥ 106,420.00 · 已核平</div>
          </div>
        </div>
      </div>
      <div class="px-1.5 pb-1 pt-4">
        <h3 class="text-[16px] font-semibold tracking-tight text-ink">对话处理表格</h3>
        <p class="mt-1 text-[13.5px] leading-relaxed text-mute">上传多份原始表，一句话完成清洗、核算与导出。</p>
      </div>
    </article>

    <!-- 2. 流程自动沉淀 -->
    <article class="reveal-item group rounded-[22px] border border-[#eae8e2] bg-white p-4 transition duration-300 hover:-translate-y-0.5 hover:shadow-[0_14px_34px_-22px_rgba(15,14,12,0.16)]" style="--reveal-delay: 60ms">
      <div class="flex h-[156px] flex-col justify-between rounded-2xl bg-[#f6f5f2] p-4">
        <div class="relative my-auto flex items-center justify-between gap-2">
          <svg class="pointer-events-none absolute inset-x-6 top-1/2 h-3 -translate-y-1/2" viewBox="0 0 220 12" fill="none">
            <line x1="0" y1="6" x2="220" y2="6" stroke="#c5c8f5" stroke-width="1.8" class="flow-dash" />
          </svg>
          <span class="relative z-10 rounded-xl border border-[#e6e4df] bg-white px-2.5 py-2 text-[11px] font-medium text-ink">读表</span>
          <span class="relative z-10 rounded-xl border border-[#cfd2f8] bg-white px-2.5 py-2 text-[11px] font-medium text-[#5255c8]">核算</span>
          <span class="relative z-10 rounded-xl border border-[#e6e4df] bg-white px-2.5 py-2 text-[11px] font-medium text-ink">交付</span>
        </div>
        <div class="flex items-center justify-between">
          <span class="rounded-full bg-white px-2.5 py-0.5 font-mono text-[10.5px] text-mute">v3 · 12s 一键复跑</span>
          <Mascot variant="party-wink" :size="40" :shadow="false" class="transition-transform duration-300 group-hover:-translate-y-1" />
        </div>
      </div>
      <div class="px-1.5 pb-1 pt-4">
        <h3 class="text-[16px] font-semibold tracking-tight text-ink">流程自动沉淀</h3>
        <p class="mt-1 text-[13.5px] leading-relaxed text-mute">跑通一次自动存为流程，新周期账单一键复跑。</p>
      </div>
    </article>

    <!-- 3. 客户规则与映射 -->
    <article class="reveal-item group rounded-[22px] border border-[#eae8e2] bg-white p-4 transition duration-300 hover:-translate-y-0.5 hover:shadow-[0_14px_34px_-22px_rgba(15,14,12,0.16)]" style="--reveal-delay: 120ms">
      <div class="flex h-[156px] items-center gap-3 rounded-2xl bg-[#f6f5f2] p-4">
        <Mascot variant="cream-shy" :size="52" :shadow="false" class="shrink-0 transition-transform duration-300 group-hover:-translate-y-1" />
        <div class="flex-1 space-y-1.5 text-[11.5px]">
          <div class="flex items-center justify-between rounded-xl border border-[#e6e4df] bg-white px-2.5 py-2">
            <span class="font-medium text-ink">运单编号</span>
            <ArrowRight class="size-3 text-[#6265dc]" />
            <span class="text-mute">快递单号</span>
          </div>
          <div class="flex items-center justify-between rounded-xl border border-[#e6e4df] bg-white px-2.5 py-2">
            <span class="font-medium text-ink">结算金额</span>
            <ArrowRight class="size-3 text-[#6265dc]" />
            <span class="text-mute">实收费用</span>
          </div>
        </div>
      </div>
      <div class="px-1.5 pb-1 pt-4">
        <h3 class="text-[16px] font-semibold tracking-tight text-ink">客户规则与映射</h3>
        <p class="mt-1 text-[13.5px] leading-relaxed text-mute">记住各客户口径与字段别名，换表也能精准对齐。</p>
      </div>
    </article>

    <!-- 4. 在线预览与比对 -->
    <article class="reveal-item group rounded-[22px] border border-[#eae8e2] bg-white p-4 transition duration-300 hover:-translate-y-0.5 hover:shadow-[0_14px_34px_-22px_rgba(15,14,12,0.16)]">
      <div class="flex h-[156px] flex-col justify-between rounded-2xl bg-[#f6f5f2] p-4">
        <div class="overflow-hidden rounded-xl border border-[#e5e3de] bg-white font-mono text-[11px] tabular-nums">
          <div class="flex items-center justify-between border-b border-[#f0eee9] bg-[#fffbeb] px-3 py-2">
            <span class="text-faint line-through">¥ 94.00</span>
            <span class="font-semibold text-[#c26d09]">¥ 106.50</span>
          </div>
          <div class="flex items-center justify-between bg-[#effcf4] px-3 py-2">
            <span class="text-mute">新增行</span>
            <span class="font-semibold text-[#158752]">+ ¥ 68.00</span>
          </div>
        </div>
        <div class="flex items-center justify-between">
          <span class="text-[11px] text-mute">逐格差异高亮</span>
          <Mascot variant="indigo-nerd" :size="40" :shadow="false" class="transition-transform duration-300 group-hover:-translate-y-1" />
        </div>
      </div>
      <div class="px-1.5 pb-1 pt-4">
        <h3 class="text-[16px] font-semibold tracking-tight text-ink">在线预览与比对</h3>
        <p class="mt-1 text-[13.5px] leading-relaxed text-mute">内置电子表格预览与双表比对，变动一眼核实。</p>
      </div>
    </article>

    <!-- 5. 本地脱敏保护 -->
    <article class="reveal-item group rounded-[22px] border border-[#eae8e2] bg-white p-4 transition duration-300 hover:-translate-y-0.5 hover:shadow-[0_14px_34px_-22px_rgba(15,14,12,0.16)]" style="--reveal-delay: 60ms">
      <div class="flex h-[156px] flex-col justify-between rounded-2xl bg-[#f6f5f2] p-4">
        <div class="flex items-center justify-between">
          <span class="inline-flex items-center gap-1 text-[11.5px] font-medium text-[#158752]">
            <ShieldCheck class="size-3.5" /> 本地替换
          </span>
          <button
            type="button"
            class="rounded-full border border-line bg-white px-2.5 py-0.5 text-[10.5px] text-ink transition hover:border-ink/40"
            @click="masked = !masked"
          >
            {{ masked ? '看原文' : '看脱敏' }}
          </button>
        </div>
        <div class="flex items-center justify-between rounded-xl border border-[#e6e4df] bg-white px-3 py-2.5 text-[12px]">
          <span class="text-mute">手机号</span>
          <span class="font-mono text-[11.5px]" :class="masked ? 'text-[#158752]' : 'text-ink'">
            {{ masked ? '[PHONE_01]' : '138-6612-8821' }}
          </span>
        </div>
        <div class="flex items-center justify-between">
          <span class="text-[11px] text-mute">交付时自动还原</span>
          <Mascot variant="sage-leaf" :size="38" :shadow="false" class="transition-transform duration-300 group-hover:-translate-y-1" />
        </div>
      </div>
      <div class="px-1.5 pb-1 pt-4">
        <h3 class="text-[16px] font-semibold tracking-tight text-ink">本地脱敏保护</h3>
        <p class="mt-1 text-[13.5px] leading-relaxed text-mute">敏感字段本地替换后再发模型，全程透明可查。</p>
      </div>
    </article>

    <!-- 6. 公式库与试算 -->
    <article class="reveal-item group rounded-[22px] border border-[#eae8e2] bg-white p-4 transition duration-300 hover:-translate-y-0.5 hover:shadow-[0_14px_34px_-22px_rgba(15,14,12,0.16)]" style="--reveal-delay: 120ms">
      <div class="flex h-[156px] flex-col justify-between rounded-2xl bg-[#f6f5f2] p-4">
        <div class="rounded-xl border border-[#e6e4df] bg-white px-3 py-2 font-mono text-[11px] text-ink">
          =IF(重&gt;3, 8+(重-3)*2.5, 8)
        </div>
        <div class="flex items-end justify-between">
          <div class="space-y-1.5">
            <div class="flex gap-1">
              <button
                v-for="w in [2.5, 6, 12]"
                :key="w"
                type="button"
                class="rounded-full border px-2 py-0.5 font-mono text-[10.5px] transition"
                :class="weight === w ? 'border-ink bg-ink text-white' : 'border-line bg-white text-mute hover:text-ink'"
                @click="weight = w"
              >
                {{ w }}kg
              </button>
            </div>
            <div class="font-mono text-[18px] font-semibold tabular-nums text-[#158752]">¥ {{ price }}</div>
          </div>
          <Mascot variant="party-money" :size="44" :shadow="false" class="transition-transform duration-300 group-hover:-translate-y-1" />
        </div>
      </div>
      <div class="px-1.5 pb-1 pt-4">
        <h3 class="text-[16px] font-semibold tracking-tight text-ink">公式库与试算</h3>
        <p class="mt-1 text-[13.5px] leading-relaxed text-mute">常用结算公式沉淀复用，支持自然语言生成试算。</p>
      </div>
    </article>
  </div>
</template>

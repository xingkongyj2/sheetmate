<script setup lang="ts">
/**
 * 彩色小物件插画（原创），每一个都对应一种表格操作：
 * sheet 表格 · funnel 筛选 · magnet 匹配 · sigma 汇总 · puzzle 合并 · rocket 执行 · folder 文件
 * crown 排名 · plane 导出 · pie 分析 · spark AI · broom 清洗 · blocks 重塑 · calc 计算 · code 自定义
 */
export type FunKind =
  | 'sheet' | 'funnel' | 'magnet' | 'sigma' | 'puzzle' | 'rocket' | 'folder' | 'crown'
  | 'plane' | 'pie' | 'spark' | 'broom' | 'blocks' | 'calc' | 'code'

withDefaults(defineProps<{ kind: FunKind; size?: number }>(), { size: 48 })
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 48 48" aria-hidden="true">
    <!-- 表格：绿色，带立体侧边 -->
    <g v-if="kind === 'sheet'">
      <rect x="9" y="10" width="34" height="31" rx="6" fill="#138a52" />
      <rect x="5" y="6" width="34" height="31" rx="6" fill="#22b573" />
      <path d="M5 12a6 6 0 0 1 6-6h22a6 6 0 0 1 6 6v3H5z" fill="#169a5e" />
      <path d="M16.5 15v22M27.5 15v22M5 22.5h34M5 30h34" stroke="#fff" stroke-opacity=".55" stroke-width="1.6" />
    </g>
    <!-- 漏斗：蓝色 -->
    <g v-else-if="kind === 'funnel'">
      <path d="M7 9h34a2 2 0 0 1 1.6 3.2L29 30v9.5l-10 4V30L5.4 12.2A2 2 0 0 1 7 9z" fill="#2f7bff" />
      <path d="M7 9h34a2 2 0 0 1 1.6 3.2L40 16H8l-2.6-3.8A2 2 0 0 1 7 9z" fill="#7fb0ff" />
      <path d="M19 30h10v1.5l-10 4z" fill="#1f5fd6" />
    </g>
    <!-- 磁铁：红色 -->
    <g v-else-if="kind === 'magnet'">
      <path d="M8 7h10v17a6 6 0 0 0 12 0V7h10v17a16 16 0 0 1-32 0z" fill="#ff5a5f" />
      <path d="M8 24a16 16 0 0 0 32 0h-4a12 12 0 0 1-24 0z" fill="#e2434a" />
      <rect x="8" y="7" width="10" height="7" rx="1.5" fill="#e6e7ec" />
      <rect x="30" y="7" width="10" height="7" rx="1.5" fill="#e6e7ec" />
    </g>
    <!-- 求和硬币：黄色 -->
    <g v-else-if="kind === 'sigma'">
      <circle cx="26" cy="26" r="18" fill="#e7a90f" />
      <circle cx="23" cy="23" r="18" fill="#ffc531" />
      <circle cx="23" cy="23" r="13" fill="#ffd96b" />
      <path d="M29 15H17l7 8-7 8h12" stroke="#a87a06" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round" />
    </g>
    <!-- 拼图：天蓝 -->
    <g v-else-if="kind === 'puzzle'">
      <path d="M11 13h8a5 5 0 1 1 10 0h8v8a5 5 0 1 1 0 10v8H29a5 5 0 1 0-10 0h-8z" fill="#1fa5d8" transform="translate(3 3)" />
      <path d="M11 13h8a5 5 0 1 1 10 0h8v8a5 5 0 1 1 0 10v8H29a5 5 0 1 0-10 0h-8z" fill="#3fc1f3" />
    </g>
    <!-- 火箭：粉色 -->
    <g v-else-if="kind === 'rocket'">
      <path d="M15 33l-6 6 1-9z" fill="#ff7a1a" />
      <path d="M33 9c6-1 7 0 6 6-1 7-7 15-15 19l-10-10C18 16 26 10 33 9z" fill="#ff6fae" />
      <path d="M14 24l-6-1 6-7 6 2zM24 34l1 6 7-6-2-6z" fill="#e14d8f" />
      <circle cx="29.5" cy="18.5" r="4.5" fill="#fff" />
      <circle cx="29.5" cy="18.5" r="2.6" fill="#2f7bff" />
    </g>
    <!-- 文件夹：黄色 -->
    <g v-else-if="kind === 'folder'">
      <path d="M5 13a4 4 0 0 1 4-4h9l4 4h17a4 4 0 0 1 4 4v19a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z" fill="#f2a21b" />
      <path d="M5 19h38v17a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z" fill="#ffc531" />
      <rect x="12" y="25" width="12" height="3" rx="1.5" fill="#fff" opacity=".7" />
    </g>
    <!-- 皇冠：紫色 -->
    <g v-else-if="kind === 'crown'">
      <path d="M8 33L5 13l11 8 8-12 8 12 11-8-3 20z" fill="#8b6cf6" />
      <rect x="8" y="33" width="32" height="6" rx="2" fill="#6d4fe0" />
      <circle cx="16" cy="28" r="2.2" fill="#ffc531" />
      <circle cx="24" cy="26" r="2.6" fill="#ff6fae" />
      <circle cx="32" cy="28" r="2.2" fill="#ffc531" />
    </g>
    <!-- 纸飞机：橙色 -->
    <g v-else-if="kind === 'plane'">
      <path d="M5 22L43 6 33 42l-9-11z" fill="#ff7a1a" />
      <path d="M24 31l19-25-23 21z" fill="#e0600b" />
      <path d="M20 27l4 4-5 8z" fill="#c4520a" />
    </g>
    <!-- 饼图：紫 + 橙 -->
    <g v-else-if="kind === 'pie'">
      <circle cx="22" cy="26" r="16" fill="#8b6cf6" />
      <path d="M26 6a16 16 0 0 1 16 16H26z" fill="#ff7a1a" />
      <path d="M22 26V10a16 16 0 0 0-11.3 27.3z" fill="#a993ff" />
    </g>
    <!-- 火花：黄色 -->
    <g v-else-if="kind === 'spark'">
      <path d="M24 4l4.5 13.5L42 22l-13.5 4.5L24 40l-4.5-13.5L6 22l13.5-4.5z" fill="#ffc531" />
      <path d="M38 34l1.6 4.4L44 40l-4.4 1.6L38 46l-1.6-4.4L32 40l4.4-1.6z" fill="#ff6fae" />
    </g>
    <!-- 扫帚：清洗 -->
    <g v-else-if="kind === 'broom'">
      <path d="M33 6l4 3-11 15-4-3z" fill="#c78a4a" />
      <path d="M20 20l9 7c-2 8-8 14-17 15 3-3 4-6 4-9-4 1-7 1-10 0 6-3 10-8 14-13z" fill="#ffc531" />
      <path d="M20 20l9 7-2 3-9-7z" fill="#ff7a1a" />
    </g>
    <!-- 积木：重塑 -->
    <g v-else-if="kind === 'blocks'">
      <rect x="6" y="24" width="16" height="16" rx="3" fill="#2f7bff" />
      <rect x="24" y="24" width="16" height="16" rx="3" fill="#ff5a5f" />
      <rect x="15" y="7" width="16" height="16" rx="3" fill="#22b573" transform="rotate(12 23 15)" />
    </g>
    <!-- 计算器：计算 -->
    <g v-else-if="kind === 'calc'">
      <rect x="11" y="6" width="28" height="38" rx="6" fill="#3a3a40" />
      <rect x="9" y="4" width="28" height="38" rx="6" fill="#55555c" />
      <rect x="14" y="9" width="18" height="8" rx="2" fill="#b8f5d5" />
      <g fill="#fff">
        <circle cx="17" cy="24" r="2.2" /><circle cx="23" cy="24" r="2.2" /><circle cx="29" cy="24" r="2.2" fill="#ff7a1a" />
        <circle cx="17" cy="31" r="2.2" /><circle cx="23" cy="31" r="2.2" /><circle cx="29" cy="31" r="2.2" fill="#ff7a1a" />
        <circle cx="17" cy="37" r="2.2" /><circle cx="23" cy="37" r="2.2" /><circle cx="29" cy="37" r="2.2" fill="#ffc531" />
      </g>
    </g>
    <!-- 代码：自定义 -->
    <g v-else-if="kind === 'code'">
      <rect x="5" y="9" width="38" height="30" rx="6" fill="#111113" />
      <path d="M17 19l-5 5 5 5M31 19l5 5-5 5M26 17l-4 14" stroke="#5fd39a" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round" />
    </g>
  </svg>
</template>

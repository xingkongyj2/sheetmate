<script setup lang="ts">
/**
 * 流程卡片、流程图步骤与公式库统一极简双色图标（墨黑主线 + 翠绿点缀，白底圆角细框）：
 * - workflow：流程卡片头（墨黑节点 → 翠绿节点）
 * - code：Python 脚本（墨黑终端框 + 翠绿光标）
 * - sql：SQL 查询（墨黑数据柱 + 翠绿表头微底）
 * - output：输出交付文件（深底上白线表格 + 翠绿核平小勾，常配 bare 使用）
 * - sigma / funnel / magnet / puzzle / broom / calc / crown / blocks / spark：各类表格公式步骤
 */
import { computed } from 'vue'

export type RecipeIconKind =
  | 'workflow'
  | 'code'
  | 'sql'
  | 'output'
  | 'sigma'
  | 'funnel'
  | 'magnet'
  | 'puzzle'
  | 'broom'
  | 'calc'
  | 'crown'
  | 'blocks'
  | 'spark'

const props = withDefaults(
  defineProps<{
    kind: RecipeIconKind
    size?: number
    /** true 时不渲染外层白底圆角框（如黑色输出胶囊内） */
    bare?: boolean
  }>(),
  { size: 24, bare: false },
)

const glyphSize = computed(() => (props.bare ? props.size : Math.round(props.size * 0.6)))
const radius = computed(() => Math.max(6, Math.round(props.size * 0.28)))
</script>

<template>
  <span
    class="inline-flex shrink-0 items-center justify-center select-none"
    :class="bare ? '' : 'recipe-icon-box'"
    :style="bare ? { width: `${size}px`, height: `${size}px` } : { width: `${size}px`, height: `${size}px`, borderRadius: `${radius}px` }"
    aria-hidden="true"
  >
    <!-- 1. 流程卡片头：墨黑节点 → 翠绿节点 -->
    <svg v-if="kind === 'workflow'" :width="glyphSize" :height="glyphSize" viewBox="0 0 24 24" fill="none">
      <rect x="3" y="3.5" width="7.5" height="7.5" rx="2.2" fill="#F4F3EE" stroke="#18181B" stroke-width="1.9" />
      <rect x="13.5" y="13" width="7.5" height="7.5" rx="2.2" fill="#E8F8F0" stroke="#22B573" stroke-width="1.9" />
      <path d="M10.5 7.25h4.2a2.5 2.5 0 0 1 2.5 2.5V13" stroke="#18181B" stroke-width="1.85" stroke-linecap="round" />
      <path d="M6.75 11v3.25a2.5 2.5 0 0 0 2.5 2.5H13.5" stroke="#18181B" stroke-width="1.5" stroke-opacity="0.28" stroke-linecap="round" stroke-dasharray="2 2.5" />
    </svg>

    <!-- 2. Python 脚本（选项 4：墨黑终端窗口 + 翠绿光标下划线） -->
    <svg v-else-if="kind === 'code'" :width="glyphSize" :height="glyphSize" viewBox="0 0 24 24" fill="none">
      <rect x="3" y="4" width="18" height="16" rx="3.5" stroke="#18181B" stroke-width="1.9" />
      <path d="m7.5 10 2.5 2-2.5 2" stroke="#18181B" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" />
      <line x1="12.5" y1="14" x2="16.5" y2="14" stroke="#22B573" stroke-width="2.1" stroke-linecap="round" />
    </svg>

    <!-- 3. SQL 查询：墨黑数据表柱 + 翠绿顶面微底 -->
    <svg v-else-if="kind === 'sql'" :width="glyphSize" :height="glyphSize" viewBox="0 0 24 24" fill="none">
      <ellipse cx="12" cy="6" rx="7.5" ry="2.8" fill="#E8F8F0" stroke="#18181B" stroke-width="1.85" />
      <path d="M4.5 6v12c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8V6" stroke="#18181B" stroke-width="1.85" stroke-linecap="round" />
      <path d="M4.5 12c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8" stroke="#18181B" stroke-width="1.6" stroke-opacity="0.55" />
      <circle cx="15.5" cy="12" r="1.4" fill="#22B573" />
    </svg>

    <!-- 4. 输出交付表格（深底上白线表格 + 翠绿核平小勾） -->
    <svg v-else-if="kind === 'output'" :width="glyphSize" :height="glyphSize" viewBox="0 0 24 24" fill="none">
      <rect x="3.2" y="3.8" width="15.8" height="14.8" rx="3" stroke="#FFFFFF" stroke-width="1.75" />
      <path d="M3.2 8.8h15.8M8.8 8.8v9.8" stroke="#FFFFFF" stroke-width="1.45" stroke-opacity="0.58" />
      <circle cx="18.2" cy="17.8" r="4.6" fill="#22B573" stroke="#0B0B0C" stroke-width="1.4" />
      <path d="M16.2 17.8l1.3 1.3 2.6-2.6" stroke="#FFFFFF" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
    </svg>

    <!-- 5. 汇总 / 合计 Σ -->
    <svg v-else-if="kind === 'sigma'" :width="glyphSize" :height="glyphSize" viewBox="0 0 24 24" fill="none">
      <path d="M18 5.5H7.2l5.4 6.5-5.4 6.5H18" stroke="#18181B" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      <circle cx="18.2" cy="18.5" r="1.7" fill="#22B573" />
    </svg>

    <!-- 6. 条件筛选 -->
    <svg v-else-if="kind === 'funnel'" :width="glyphSize" :height="glyphSize" viewBox="0 0 24 24" fill="none">
      <path d="M4 5.5h16l-6 7v6.2l-4-2V12.5l-6-7Z" fill="#E8F8F0" stroke="#18181B" stroke-width="1.85" stroke-linejoin="round" />
      <circle cx="18" cy="17.5" r="2.1" fill="#22B573" />
    </svg>

    <!-- 7. 匹配查找（双表链接） -->
    <svg v-else-if="kind === 'magnet'" :width="glyphSize" :height="glyphSize" viewBox="0 0 24 24" fill="none">
      <path d="M10 13a4.2 4.2 0 0 0 5.9 0l2.4-2.4a4.2 4.2 0 0 0-5.9-5.9L11 6.1" stroke="#18181B" stroke-width="1.9" stroke-linecap="round" />
      <path d="M14 11a4.2 4.2 0 0 0-5.9 0l-2.4 2.4a4.2 4.2 0 0 0 5.9 5.9L13 17.9" stroke="#18181B" stroke-width="1.9" stroke-linecap="round" />
      <circle cx="12" cy="12" r="1.7" fill="#22B573" />
    </svg>

    <!-- 8. 合并表 / 拼接 -->
    <svg v-else-if="kind === 'puzzle'" :width="glyphSize" :height="glyphSize" viewBox="0 0 24 24" fill="none">
      <rect x="3.5" y="4" width="11" height="11" rx="2.5" stroke="#18181B" stroke-width="1.85" />
      <rect x="9.5" y="9" width="11" height="11" rx="2.5" fill="#E8F8F0" stroke="#18181B" stroke-width="1.85" />
      <path d="M12.5 14.5h5M15 12v5" stroke="#22B573" stroke-width="1.85" stroke-linecap="round" />
    </svg>

    <!-- 9. 清洗 / 去重 / 补空 -->
    <svg v-else-if="kind === 'broom'" :width="glyphSize" :height="glyphSize" viewBox="0 0 24 24" fill="none">
      <path d="m7 20 10-10-4-4L3 16v4h4Z" stroke="#18181B" stroke-width="1.85" stroke-linejoin="round" />
      <path d="M13 20h7" stroke="#18181B" stroke-width="1.85" stroke-linecap="round" />
      <path d="M18.2 4l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8Z" fill="#22B573" />
    </svg>

    <!-- 10. 列计算 / 映射 / 拆分 -->
    <svg v-else-if="kind === 'calc'" :width="glyphSize" :height="glyphSize" viewBox="0 0 24 24" fill="none">
      <rect x="4" y="3.5" width="16" height="17" rx="3" stroke="#18181B" stroke-width="1.85" />
      <path d="M8 8.5h8" stroke="#22B573" stroke-width="2" stroke-linecap="round" />
      <path d="M8.5 13h2M13.5 13h2M8.5 16.5h2M13.5 16.5h2" stroke="#18181B" stroke-width="1.9" stroke-linecap="round" />
    </svg>

    <!-- 11. 排序 / Top N -->
    <svg v-else-if="kind === 'crown'" :width="glyphSize" :height="glyphSize" viewBox="0 0 24 24" fill="none">
      <path d="M5 7h14" stroke="#22B573" stroke-width="2.1" stroke-linecap="round" />
      <path d="M5 12h10" stroke="#18181B" stroke-width="2" stroke-linecap="round" />
      <path d="M5 17h6" stroke="#18181B" stroke-width="2" stroke-linecap="round" />
      <path d="m16.5 15 2 2 2-2M18.5 11.5V17" stroke="#18181B" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
    </svg>

    <!-- 12. 透视 / 重塑表格 -->
    <svg v-else-if="kind === 'blocks'" :width="glyphSize" :height="glyphSize" viewBox="0 0 24 24" fill="none">
      <rect x="3.5" y="4" width="17" height="16" rx="3" stroke="#18181B" stroke-width="1.85" />
      <path d="M3.5 9.5h17M9.5 9.5V20" stroke="#18181B" stroke-width="1.65" />
      <rect x="4.5" y="5" width="15" height="3.6" rx="1.5" fill="#22B573" fill-opacity="0.25" />
    </svg>

    <!-- 13. 通用公式 -->
    <svg v-else :width="glyphSize" :height="glyphSize" viewBox="0 0 24 24" fill="none">
      <rect x="4" y="4" width="16" height="16" rx="4" stroke="#18181B" stroke-width="1.85" />
      <path d="M12 7.5 13.3 10.7 16.5 12 13.3 13.3 12 16.5 10.7 13.3 7.5 12 10.7 10.7Z" fill="#22B573" />
    </svg>
  </span>
</template>

<style scoped>
.recipe-icon-box {
  background: #ffffff;
  border: 1px solid #e4e2dc;
  box-shadow: 0 1px 3px rgba(24, 24, 27, 0.04);
}
</style>

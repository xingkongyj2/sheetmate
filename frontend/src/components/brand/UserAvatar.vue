<script setup lang="ts">
/**
 * 成员头像组件：有自定义头像时展示图片，否则按用户名 / 账号 key 自动生成多层立体柔光球体头像。
 * 尺寸通过外层 class（如 size-6 / size-8 / size-9）控制。
 */
import { computed } from 'vue'

import { resolveAvatarIcon } from './avatarIcons'

const props = defineProps<{
  /** 用于稳定生成配色的唯一键，建议传账号（account）或用户名（display_name） */
  seed?: string | null
  /** 已上传的自定义头像地址，为空时自动展示默认渐变图标 */
  src?: string | null
  /** 无障碍文本（可选） */
  alt?: string
}>()

const icon = computed(() => resolveAvatarIcon(props.seed ?? ''))
const imageSrc = computed(() => props.src || icon.value.dataUrl)
</script>

<template>
  <span
    class="relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full"
    :aria-hidden="alt ? undefined : 'true'"
  >
    <img :src="imageSrc" :alt="alt ?? ''" class="size-full object-cover select-none" draggable="false" />
  </span>
</template>

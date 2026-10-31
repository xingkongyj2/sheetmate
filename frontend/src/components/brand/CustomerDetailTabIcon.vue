<script setup lang="ts">
defineProps<{ name: 'logic' | 'recipes' | 'files' | 'runs' | 'memory' }>()
</script>

<template>
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">
    <!-- 处理逻辑：翻开的书页与叠层封面。 -->
    <g v-if="name === 'logic'">
      <path d="M3 4.5a2 2 0 0 1 2-2h4.1c1.3 0 2.5.4 3.5 1.2 1-.8 2.2-1.2 3.5-1.2H20a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-3.9c-1.3 0-2.5.4-3.5 1.2-1-.8-2.2-1.2-3.5-1.2H5a2 2 0 0 1-2-2v-14Z" fill="#C5C8FA" />
      <path d="M1.5 6a2 2 0 0 1 2-2H8a5 5 0 0 1 4 2 5 5 0 0 1 4-2h4.5a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H16a5 5 0 0 0-4 2 5 5 0 0 0-4-2H3.5a2 2 0 0 1-2-2V6Z" fill="#6265DC" />
      <path d="M12 6a5 5 0 0 1 4-2h4.5a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H16a5 5 0 0 0-4 2V6Z" fill="#7578EA" />
      <rect x="4.5" y="8" width="4.75" height="1.5" rx=".75" fill="white" />
      <rect x="4.5" y="11.5" width="4.75" height="1.5" rx=".75" fill="#C5C8FA" />
      <rect x="4.5" y="15" width="3.25" height="1.5" rx=".75" fill="#C5C8FA" />
      <rect x="14.75" y="8" width="4.75" height="1.5" rx=".75" fill="white" />
      <rect x="14.75" y="11.5" width="4.75" height="1.5" rx=".75" fill="#C5C8FA" />
      <rect x="14.75" y="15" width="3.25" height="1.5" rx=".75" fill="#C5C8FA" />
    </g>

    <!-- 流程：圆润的实心节点，连接关系保持清晰。 -->
    <g v-else-if="name === 'recipes'">
      <path d="M11 7h2v3h4.5a2 2 0 0 1 2 2v4h-2v-4H6.5v4h-2v-4a2 2 0 0 1 2-2H11V7Z" fill="#C5C8FA" />
      <rect x="8" y="1.5" width="9" height="7.5" rx="2.75" fill="#969BF2" />
      <rect x="7" y="2.5" width="9" height="7.5" rx="2.75" fill="#6265DC" />
      <circle cx="11.5" cy="6.25" r="1.25" fill="white" />
      <rect x="2" y="15" width="8" height="7.5" rx="2.75" fill="#969BF2" />
      <rect x="1" y="16" width="8" height="6.5" rx="2.5" fill="#7578EA" />
      <rect x="15" y="15" width="8" height="7.5" rx="2.75" fill="#969BF2" />
      <rect x="14" y="16" width="8" height="6.5" rx="2.5" fill="#6265DC" />
      <rect x="3.5" y="18.5" width="3" height="1.5" rx=".75" fill="white" />
      <rect x="16.5" y="18.5" width="3" height="1.5" rx=".75" fill="white" />
    </g>

    <!-- 文件：与客户库相同的多层卡片，前景是表格文档。 -->
    <g v-else-if="name === 'files'">
      <rect x="6.5" y="1.5" width="15.5" height="18" rx="4" fill="#C5C8FA" />
      <rect x="4.5" y="3.5" width="16.5" height="18" rx="4" fill="#969BF2" />
      <rect x="2" y="5.5" width="18" height="17" rx="4" fill="#6265DC" />
      <path d="M6 5.5h10a4 4 0 0 1 4 4V10H2v-.5a4 4 0 0 1 4-4Z" fill="#7578EA" />
      <rect x="5" y="12.5" width="3" height="2.5" rx=".75" fill="white" />
      <rect x="9.5" y="12.5" width="7.5" height="2.5" rx=".75" fill="#C5C8FA" />
      <rect x="5" y="17" width="3" height="2.5" rx=".75" fill="#C5C8FA" />
      <rect x="9.5" y="17" width="7.5" height="2.5" rx=".75" fill="white" />
    </g>

    <!-- 运行记录：叠层日志卡与前景时钟。 -->
    <g v-else-if="name === 'runs'">
      <rect x="5" y="1.5" width="15" height="18" rx="4" fill="#C5C8FA" />
      <rect x="2.5" y="3.5" width="15.5" height="18" rx="4" fill="#7578EA" />
      <rect x="5.5" y="7" width="8.5" height="1.5" rx=".75" fill="white" />
      <rect x="5.5" y="10.5" width="5" height="1.5" rx=".75" fill="#C5C8FA" />
      <rect x="5.5" y="14" width="3.5" height="1.5" rx=".75" fill="#C5C8FA" />
      <circle cx="16" cy="16" r="7" fill="#C5C8FA" />
      <circle cx="16" cy="16" r="5.75" fill="#6265DC" />
      <rect x="15.25" y="12.5" width="1.5" height="4.25" rx=".75" fill="white" />
      <rect x="15.25" y="15.25" width="4" height="1.5" rx=".75" fill="white" />
    </g>

    <!-- 记忆与字段：索引标签、字段行以及记忆火花。 -->
    <g v-else>
      <rect x="5" y="1.5" width="16.5" height="18.5" rx="4" fill="#C5C8FA" />
      <rect x="2" y="4" width="18.5" height="18.5" rx="4" fill="#6265DC" />
      <path d="M6 4h10.5a4 4 0 0 1 4 4v1H2V8a4 4 0 0 1 4-4Z" fill="#7578EA" />
      <rect x="19" y="8" width="3" height="4" rx="1.25" fill="#969BF2" />
      <rect x="19" y="13.5" width="3" height="4" rx="1.25" fill="#969BF2" />
      <path d="m7 10 1.1 2.4 2.4 1.1-2.4 1.1L7 17l-1.1-2.4-2.4-1.1 2.4-1.1L7 10Z" fill="white" />
      <rect x="12" y="11" width="5.5" height="1.5" rx=".75" fill="white" />
      <rect x="12" y="14.5" width="4" height="1.5" rx=".75" fill="#C5C8FA" />
      <rect x="5" y="18.5" width="12.5" height="1.5" rx=".75" fill="#C5C8FA" />
    </g>
  </svg>
</template>

<script setup lang="ts">
/**
 * 展示页页尾宽幅原创插画：主 IP 绿芽墨墨与不同配色的小团子们围在账单工作台旁协同作业。
 */
defineProps<{
  variant?: 'closing'
}>()
</script>

<template>
  <svg
    viewBox="0 0 1120 290"
    fill="none"
    class="h-auto w-full select-none overflow-visible"
    aria-hidden="true"
  >
    <defs>
      <linearGradient id="closing-fade" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0%" stop-color="#FFFFFF" stop-opacity="0" />
        <stop offset="15%" stop-color="#EFECE6" stop-opacity="0.65" />
        <stop offset="85%" stop-color="#EFECE6" stop-opacity="0.65" />
        <stop offset="100%" stop-color="#FFFFFF" stop-opacity="0" />
      </linearGradient>
      <linearGradient id="closing-desk" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#FFFFFF" />
        <stop offset="100%" stop-color="#F7F6F3" />
      </linearGradient>
    </defs>

    <!-- 地平线柔和底带 -->
    <ellipse cx="560" cy="252" rx="460" ry="18" fill="url(#closing-fade)" />

    <!-- 背景流动流程弧线 -->
    <path d="M190 156 C270 72 410 62 510 116" stroke="#C5C8F5" stroke-width="2" stroke-linecap="round" stroke-dasharray="5 6" />
    <path d="M612 114 C710 56 842 68 928 152" stroke="#A7E3C6" stroke-width="2" stroke-linecap="round" stroke-dasharray="5 6" />

    <!-- 漂浮微件 -->
    <g class="scene-float-slow">
      <g transform="translate(230 56) rotate(-7)">
        <rect width="74" height="52" rx="12" fill="#FFFFFF" stroke="#E4E2DC" stroke-width="1.4" />
        <rect x="12" y="13" width="22" height="6" rx="3" fill="#E8E6E0" />
        <rect x="39" y="13" width="23" height="6" rx="3" fill="#DCF5E8" />
        <rect x="12" y="25" width="50" height="5" rx="2.5" fill="#F1EFEA" />
        <rect x="12" y="35" width="34" height="5" rx="2.5" fill="#ECEBFA" />
      </g>
    </g>

    <g class="scene-float-slow-delay">
      <g transform="translate(808 50) rotate(6)">
        <rect width="84" height="54" rx="12" fill="#FFFFFF" stroke="#E4E2DC" stroke-width="1.4" />
        <circle cx="24" cy="27" r="11" fill="#EBF9F2" />
        <path d="M19.5 27 L22.8 30.3 L29 24" stroke="#22B573" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" />
        <rect x="42" y="19" width="30" height="5.5" rx="2.75" fill="#18181B" opacity="0.7" />
        <rect x="42" y="29" width="22" height="4.5" rx="2.25" fill="#D4D1CA" />
      </g>
    </g>

    <!-- 漂浮小星火 -->
    <path class="scene-twinkle" d="M402 52 L404.5 58.5 L411 61 L404.5 63.5 L402 70 L399.5 63.5 L393 61 L399.5 58.5 Z" fill="#6265DC" />
    <path class="scene-twinkle-delay" d="M726 48 L728 53 L733 55 L728 57 L726 62 L724 57 L719 55 L724 53 Z" fill="#FF7A1A" />
    <circle cx="348" cy="108" r="4" fill="#22B573" opacity="0.5" />
    <circle cx="776" cy="116" r="3.5" fill="#6265DC" opacity="0.45" />

    <!-- 中央智能账单工作台 -->
    <g transform="translate(362 128)">
      <rect x="0" y="0" width="396" height="122" rx="22" fill="url(#closing-desk)" stroke="#E3E1DA" stroke-width="1.6" />
      <path d="M0 22 A22 22 0 0 1 22 0 H374 A22 22 0 0 1 396 22 V32 H0 Z" fill="#F4F3EE" />
      <circle cx="22" cy="16" r="3.6" fill="#D5D2CB" />
      <circle cx="35" cy="16" r="3.6" fill="#D5D2CB" />
      <circle cx="48" cy="16" r="3.6" fill="#D5D2CB" />
      <rect x="150" y="12" width="96" height="8" rx="4" fill="#E3E1DA" />

      <rect x="24" y="48" width="76" height="14" rx="5" fill="#EFECE6" />
      <rect x="110" y="48" width="94" height="14" rx="5" fill="#EFECE6" />
      <rect x="214" y="48" width="74" height="14" rx="5" fill="#ECEBFA" />
      <rect x="298" y="48" width="74" height="14" rx="5" fill="#DCF5E8" />

      <rect x="24" y="70" width="76" height="12" rx="4" fill="#F5F4F0" />
      <rect x="110" y="70" width="94" height="12" rx="4" fill="#F5F4F0" />
      <rect x="214" y="70" width="74" height="12" rx="4" fill="#F4F3FD" />
      <rect x="298" y="70" width="74" height="12" rx="4" fill="#EDFBF4" />

      <rect x="24" y="90" width="76" height="12" rx="4" fill="#F5F4F0" />
      <rect x="110" y="90" width="94" height="12" rx="4" fill="#F5F4F0" />
      <rect x="214" y="90" width="74" height="12" rx="4" fill="#F4F3FD" />
      <rect x="298" y="90" width="74" height="12" rx="4" fill="#DCF5E8" />

      <path d="M298 109 H372" stroke="#FF7A1A" stroke-width="1.8" stroke-linecap="round" />
      <path d="M306 113.5 H368" stroke="#FF7A1A" stroke-width="1.6" stroke-linecap="round" opacity="0.65" />
    </g>

    <!-- 角色 1：靛蓝学霸小团子 -->
    <g transform="translate(154 156)">
      <g class="scene-bob-delay">
        <ellipse cx="44" cy="92" rx="30" ry="6" fill="#18181B" opacity="0.08" />
        <path d="M44 26 C44 19 46 13 49 8" stroke="#159A5D" stroke-width="2.6" stroke-linecap="round" />
        <path d="M48.5 11 C55 3 68 5 66.5 12 C60 17.5 52.5 16.5 48.5 11 Z" fill="#22B573" />
        <path d="M44 24 C66 24 78 38 78 58 C78 76 66 86 44 86 C22 86 10 76 10 58 C10 38 22 24 44 24 Z" fill="#6265DC" />
        <circle cx="32" cy="54" r="9" fill="#FFFFFF" stroke="#18181F" stroke-width="2.2" />
        <circle cx="56" cy="54" r="9" fill="#FFFFFF" stroke="#18181F" stroke-width="2.2" />
        <path d="M41 54 H47" stroke="#18181F" stroke-width="2.2" />
        <circle cx="34" cy="54" r="3.4" fill="#18181F" />
        <circle cx="58" cy="54" r="3.4" fill="#18181F" />
        <path d="M39 69 Q44 73 49 69" stroke="#FFFFFF" stroke-width="2.2" stroke-linecap="round" />
      </g>
    </g>

    <!-- 角色 2：蓝波点尖帽小团子 -->
    <g transform="translate(274 146)">
      <g class="scene-bob">
        <ellipse cx="48" cy="100" rx="34" ry="6.5" fill="#18181B" opacity="0.09" />
        <g transform="rotate(-8 48 26)">
          <path d="M32 28 L48 -2 L64 28 Q48 33 32 28 Z" fill="#4C7CF0" />
          <circle cx="44" cy="18" r="2.2" fill="#FFFFFF" opacity="0.88" />
          <circle cx="54" cy="20" r="1.9" fill="#FFFFFF" opacity="0.82" />
          <circle cx="48" cy="-4" r="4.4" fill="#FFC531" />
        </g>
        <path d="M48 26 C73 26 88 42 88 64 C88 84 73 95 48 95 C23 95 8 84 8 64 C8 42 23 26 48 26 Z" fill="#111113" />
        <ellipse cx="36" cy="60" rx="7.6" ry="9.2" fill="#FFFFFF" />
        <circle cx="38.5" cy="59" r="3.8" fill="#111113" />
        <path d="M54 60 Q61 53 68 60" stroke="#FFFFFF" stroke-width="3.2" stroke-linecap="round" />
        <path d="M43 75 Q48 80 53 75" stroke="#FFFFFF" stroke-width="2.4" stroke-linecap="round" />
      </g>
    </g>

    <!-- 角色 3：居中探头的绿芽墨墨 -->
    <g transform="translate(504 54)">
      <g class="scene-bob">
        <path d="M56 28 C56 17 58 9 62 2" stroke="#159A5D" stroke-width="3.4" stroke-linecap="round" />
        <path d="M61.5 6 C70 -6 89 -4 87 6 C78 14 67 13 61.5 6 Z" fill="#22B573" />
        <path d="M59 11 C52 1 38 3 39 11 C46 17 54 16 59 11 Z" fill="#5FD39A" />
        <path d="M56 26 C84 26 100 45 100 72 C100 73.5 99.9 75 99.7 76 H12.3 C12.1 75 12 73.5 12 72 C12 45 28 26 56 26 Z" fill="#111113" />
        <g class="scene-blink">
          <ellipse cx="42" cy="58" rx="9.5" ry="11.2" fill="#FFFFFF" />
          <ellipse cx="70" cy="58" rx="9.5" ry="11.2" fill="#FFFFFF" />
          <circle cx="44" cy="58.5" r="4.6" fill="#111113" />
          <circle cx="72" cy="58.5" r="4.6" fill="#111113" />
          <circle cx="45.8" cy="56" r="1.7" fill="#FFFFFF" />
          <circle cx="73.8" cy="56" r="1.7" fill="#FFFFFF" />
        </g>
        <ellipse cx="22" cy="76" rx="9" ry="6.5" fill="#111113" />
        <ellipse cx="90" cy="76" rx="9" ry="6.5" fill="#111113" />
      </g>
    </g>

    <!-- 角色 4：云朵奶白小团子 -->
    <g transform="translate(766 150)">
      <g class="scene-bob-delay">
        <ellipse cx="46" cy="96" rx="32" ry="6" fill="#18181B" opacity="0.08" />
        <path d="M46 25 C46 18 48 12 51 7" stroke="#159A5D" stroke-width="2.6" stroke-linecap="round" />
        <path d="M50.5 10 C57 2 70 4 68.5 11 C62 16.5 54.5 15.5 50.5 10 Z" fill="#22B573" />
        <path d="M46 24 C70 24 84 40 84 61 C84 80 70 91 46 91 C22 91 8 80 8 61 C8 40 22 24 46 24 Z" fill="#FFFDF8" stroke="#1D1D22" stroke-width="2.4" />
        <path d="M27 59 Q34 51 41 59" stroke="#1D1D22" stroke-width="3" stroke-linecap="round" />
        <path d="M51 59 Q58 51 65 59" stroke="#1D1D22" stroke-width="3" stroke-linecap="round" />
        <ellipse cx="24" cy="67" rx="5.5" ry="2.8" fill="#FFB0A6" opacity="0.72" />
        <ellipse cx="68" cy="67" rx="5.5" ry="2.8" fill="#FFB0A6" opacity="0.72" />
        <path d="M40 70 Q46 75.5 52 70" stroke="#1D1D22" stroke-width="2.4" stroke-linecap="round" />
      </g>
    </g>

    <!-- 角色 5：蜜桃奶橘小团子 -->
    <g transform="translate(886 160)">
      <g class="scene-bob">
        <ellipse cx="42" cy="88" rx="28" ry="5.5" fill="#18181B" opacity="0.08" />
        <path d="M42 23 C42 16 44 10 47 6" stroke="#159A5D" stroke-width="2.4" stroke-linecap="round" />
        <path d="M46.5 9 C52 2 64 4 62.5 10.5 C57 15.5 50 14.5 46.5 9 Z" fill="#22B573" />
        <path d="M42 22 C63 22 75 36 75 55 C75 72 63 82 42 82 C21 82 9 72 9 55 C9 36 21 22 42 22 Z" fill="#F29B72" />
        <path d="M26 53 Q32 46 38 53" stroke="#5E2711" stroke-width="2.8" stroke-linecap="round" />
        <path d="M46 53 Q52 46 58 53" stroke="#5E2711" stroke-width="2.8" stroke-linecap="round" />
        <path d="M35 63 Q42 71 49 63 Z" fill="#5E2711" />
        <ellipse cx="22" cy="60" rx="4.5" ry="2.4" fill="#FF7D8D" opacity="0.6" />
        <ellipse cx="62" cy="60" rx="4.5" ry="2.4" fill="#FF7D8D" opacity="0.6" />
      </g>
    </g>
  </svg>
</template>

<style scoped>
.scene-float-slow {
  animation: scene-float 6.2s ease-in-out infinite;
}
.scene-float-slow-delay {
  animation: scene-float 6.8s ease-in-out 1.1s infinite;
}
.scene-bob {
  animation: scene-bob 4.4s ease-in-out infinite;
}
.scene-bob-delay {
  animation: scene-bob 4.8s ease-in-out 0.9s infinite;
}
.scene-blink {
  transform-box: fill-box;
  transform-origin: center;
  animation: scene-blink 5.2s ease-in-out infinite;
}
.scene-twinkle {
  transform-box: fill-box;
  transform-origin: center;
  animation: scene-twinkle 3s ease-in-out infinite;
}
.scene-twinkle-delay {
  transform-box: fill-box;
  transform-origin: center;
  animation: scene-twinkle 3.4s ease-in-out 1s infinite;
}

@keyframes scene-float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-7px); }
}
@keyframes scene-bob {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-5px); }
}
@keyframes scene-blink {
  0%, 92%, 100% { transform: scaleY(1); }
  96% { transform: scaleY(0.1); }
}
@keyframes scene-twinkle {
  0%, 100% { opacity: 0.6; transform: scale(0.88) rotate(0deg); }
  50% { opacity: 1; transform: scale(1.12) rotate(12deg); }
}

@media (prefers-reduced-motion: reduce) {
  .scene-float-slow,
  .scene-float-slow-delay,
  .scene-bob,
  .scene-bob-delay,
  .scene-blink,
  .scene-twinkle,
  .scene-twinkle-delay {
    animation: none;
  }
}
</style>

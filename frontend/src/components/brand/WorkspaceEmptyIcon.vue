<script setup lang="ts">
/** 工作区面板三个空态（流程 / 文件 / 数据）的原创小墨墨插画图标。 */
import { useId } from 'vue'

defineProps<{ kind: 'recipes' | 'files' | 'data' }>()

const uid = useId()
const sheetClipId = `ws-empty-sheet-${uid}`
</script>

<template>
  <svg
    class="ws-empty-icon h-[92px] w-[116px] overflow-visible select-none"
    viewBox="0 0 120 96"
    fill="none"
    aria-hidden="true"
    focusable="false"
  >
    <!-- 流程：小墨墨举着节点火花，好奇看着待连线的虚线流程槽 -->
    <g v-if="kind === 'recipes'">
      <!-- 柔和背景衬底与装饰点 -->
      <ellipse cx="60" cy="52" rx="44" ry="30" fill="#F4F3EE" />
      <circle cx="22" cy="24" r="2.2" fill="#DCDAF8" />
      <circle cx="99" cy="68" r="2.6" fill="#E6E4DC" />
      <circle cx="103" cy="22" r="1.8" fill="#C5C8FA" />

      <!-- 虚线连接路径 -->
      <path
        class="ws-dash"
        d="M47 41 C57 41 60 31 71 31"
        stroke="#969BF2"
        stroke-width="2.2"
        stroke-linecap="round"
        stroke-dasharray="4 4"
      />

      <!-- 左侧已有流程节点卡片 -->
      <rect x="13" y="28" width="34" height="25" rx="7.5" fill="#FFFFFF" stroke="#E3E1DA" stroke-width="1.5" />
      <circle cx="21.5" cy="36.5" r="2.8" fill="#6265DC" />
      <rect x="27" y="34.7" width="13.5" height="3.2" rx="1.6" fill="#D4D6FA" />
      <rect x="19" y="43.5" width="21" height="3" rx="1.5" fill="#ECEAE3" />

      <!-- 右侧虚线空节点槽 -->
      <rect
        x="72"
        y="18"
        width="34"
        height="25"
        rx="7.5"
        fill="#F6F7FE"
        stroke="#969BF2"
        stroke-width="1.6"
        stroke-dasharray="4 3"
      />
      <path d="M89 26.2 v8.6 M84.7 30.5 h8.6" stroke="#6265DC" stroke-width="1.9" stroke-linecap="round" />

      <!-- 小墨墨地面投影 -->
      <ellipse cx="59" cy="86.5" rx="20" ry="3.2" fill="#18181B" opacity="0.08" />

      <!-- 小墨墨本体 -->
      <g class="ws-bob">
        <!-- 招牌小绿芽 -->
        <path d="M59 49 C59 45 59.5 42 60.5 39" stroke="#159A5D" stroke-width="2" stroke-linecap="round" />
        <path d="M60.5 40.5 C63.5 35 71 36 70.5 40 C67 43.2 62.5 43 60.5 40.5 Z" fill="#22B573" />
        <path d="M59.8 42.2 C57.2 38 51.5 38.8 52 41.8 C54.8 44.2 58 44 59.8 42.2 Z" fill="#5FD39A" />

        <!-- 左右小手（右手举着小火花） -->
        <ellipse cx="40.5" cy="72" rx="3.8" ry="5.5" fill="#18181B" transform="rotate(-24 40.5 72)" />
        <ellipse cx="78.5" cy="64.5" rx="3.8" ry="5.8" fill="#18181B" transform="rotate(38 78.5 64.5)" />

        <!-- 圆团子身体 -->
        <path
          d="M59 48 C72 48 80 57 80 69 C80 79.5 72 85 59 85 C46 85 38 79.5 38 69 C38 57 46 48 59 48 Z"
          fill="#18181B"
        />

        <!-- 好奇朝右上方看的大眼睛 -->
        <g class="ws-blink">
          <ellipse cx="52.5" cy="66" rx="5.2" ry="6.2" fill="#FFFFFF" />
          <ellipse cx="65.5" cy="66" rx="5.2" ry="6.2" fill="#FFFFFF" />
          <circle cx="54.2" cy="64.4" r="2.7" fill="#18181B" />
          <circle cx="67.2" cy="64.4" r="2.7" fill="#18181B" />
          <circle cx="55.3" cy="63.1" r="1" fill="#FFFFFF" />
          <circle cx="68.3" cy="63.1" r="1" fill="#FFFFFF" />
        </g>

        <!-- 小嘴：轻微思考的「o」感 -->
        <circle cx="59" cy="74.2" r="1.4" fill="#FFFFFF" opacity="0.72" />

        <!-- 右手边漂浮的紫色小星火 -->
        <path
          class="ws-twinkle"
          d="M87 50 L88.6 54.4 L93 56 L88.6 57.6 L87 62 L85.4 57.6 L81 56 L85.4 54.4 Z"
          fill="#6265DC"
        />
      </g>
    </g>

    <!-- 文件：空文件夹里探出头、双爪扒在边缘张望的小墨墨 -->
    <g v-else-if="kind === 'files'">
      <!-- 漂浮纸飞机与虚线航迹 -->
      <g class="ws-plane">
        <path d="M77 27 C82 22 88 21 92 18" stroke="#C5C8FA" stroke-width="1.6" stroke-linecap="round" stroke-dasharray="2.5 3" />
        <path d="M90 14 L105 9 L98.5 22 L95.5 17.2 L90 14 Z" fill="#6265DC" />
        <path d="M105 9 L95.5 17.2 L94.8 21.5 L98.5 18.8" fill="#969BF2" />
      </g>
      <circle cx="20" cy="24" r="2.2" fill="#E6E4DC" />
      <circle cx="101" cy="39" r="2" fill="#DCDAF8" />

      <!-- 地面投影 -->
      <ellipse cx="59" cy="86" rx="34" ry="3.5" fill="#18181B" opacity="0.08" />

      <!-- 文件夹背板 -->
      <path
        d="M23 32 A6.5 6.5 0 0 1 29.5 25.5 H45 A6 6 0 0 1 49.6 27.6 L52.6 31 H88.5 A6.5 6.5 0 0 1 95 37.5 V75 A8 8 0 0 1 87 83 H31 A8 8 0 0 1 23 75 Z"
        fill="#C5C8FA"
      />
      <!-- 文件夹内部暗槽 -->
      <rect x="27" y="35" width="64" height="36" rx="6" fill="#8C91EC" />

      <!-- 从文件夹口袋探出头的小墨墨 -->
      <g class="ws-peek">
        <!-- 招牌小绿芽 -->
        <path d="M59 27 C59 23.5 59.5 20.5 60.6 17.5" stroke="#159A5D" stroke-width="2" stroke-linecap="round" />
        <path d="M60.6 19 C63.6 13.8 71 14.8 70.5 18.8 C67 21.8 62.5 21.6 60.6 19 Z" fill="#22B573" />
        <path d="M59.8 20.8 C57.2 16.8 51.6 17.6 52.1 20.4 C54.8 22.8 58 22.6 59.8 20.8 Z" fill="#5FD39A" />

        <!-- 露出的上半身 -->
        <path
          d="M59 26.5 C71.8 26.5 79.5 34.5 79.5 46.5 V56 H38.5 V46.5 C38.5 34.5 46.2 26.5 59 26.5 Z"
          fill="#18181B"
        />

        <!-- 呆萌眨眼的大眼睛 -->
        <g class="ws-blink">
          <ellipse cx="52.5" cy="40.5" rx="5.1" ry="6" fill="#FFFFFF" />
          <ellipse cx="65.5" cy="40.5" rx="5.1" ry="6" fill="#FFFFFF" />
          <circle cx="53.6" cy="39.8" r="2.6" fill="#18181B" />
          <circle cx="66.6" cy="39.8" r="2.6" fill="#18181B" />
          <circle cx="54.6" cy="38.5" r="1" fill="#FFFFFF" />
          <circle cx="67.6" cy="38.5" r="1" fill="#FFFFFF" />
        </g>
      </g>

      <!-- 文件夹前盖板（略带透视感） -->
      <path
        d="M19.5 53.5 A6.5 6.5 0 0 1 26 47.5 H92 A6.5 6.5 0 0 1 98.5 53.5 L95 76.5 A8 8 0 0 1 87.1 83.5 H30.9 A8 8 0 0 1 23 76.5 Z"
        fill="#FFFFFF"
        stroke="#DFDDD6"
        stroke-width="1.6"
      />
      <!-- 前盖板装饰线 -->
      <rect x="32" y="61" width="18" height="3.2" rx="1.6" fill="#E3E5FC" />
      <rect x="32" y="67.5" width="11" height="3" rx="1.5" fill="#F0EFEA" />
      <circle cx="84.5" cy="69" r="3.5" fill="#F4F3EE" />
      <circle cx="84.5" cy="69" r="1.6" fill="#969BF2" />

      <!-- 扒在前盖板边缘的一对小黑爪 -->
      <ellipse cx="43.5" cy="48" rx="5.2" ry="3.4" fill="#18181B" />
      <ellipse cx="74.5" cy="48" rx="5.2" ry="3.4" fill="#18181B" />
    </g>

    <!-- 数据：趴在空白表格上打盹冒泡的小墨墨 -->
    <g v-else>
      <defs>
        <clipPath :id="sheetClipId">
          <rect x="18" y="23" width="84" height="58" rx="10" />
        </clipPath>
      </defs>

      <!-- 地面投影 -->
      <ellipse cx="60" cy="86.5" rx="37" ry="3.5" fill="#18181B" opacity="0.08" />

      <!-- 空白表格卡片 -->
      <rect x="18" y="23" width="84" height="58" rx="10" fill="#FFFFFF" stroke="#E2E0D9" stroke-width="1.6" />
      <g :clip-path="`url(#${sheetClipId})`">
        <!-- 表头底色 -->
        <rect x="18" y="23" width="84" height="15" fill="#F2F3FD" />
        <!-- 表头占位胶囊 -->
        <rect x="25" y="28.7" width="14" height="3.6" rx="1.8" fill="#6265DC" opacity="0.85" />
        <rect x="52" y="28.7" width="16" height="3.6" rx="1.8" fill="#A9AEF6" />
        <rect x="80" y="28.7" width="13" height="3.6" rx="1.8" fill="#C5C8FA" />

        <!-- 空白网格线 -->
        <path
          d="M18 38 H102 M18 52 H102 M18 66 H102 M46 38 V81 M74 38 V81"
          stroke="#EEECE6"
          stroke-width="1.3"
        />
      </g>

      <!-- 趴在表格上打盹的扁圆团子墨墨 -->
      <g class="ws-breathe">
        <!-- 微微歪着的绿芽 -->
        <g transform="rotate(14 60 49)">
          <path d="M60 49 C60 45.5 60.5 42.5 61.5 39.5" stroke="#159A5D" stroke-width="2" stroke-linecap="round" />
          <path d="M61.5 41 C64.5 36 71.5 37 71 40.8 C67.5 43.8 63.3 43.6 61.5 41 Z" fill="#22B573" />
          <path d="M60.8 42.6 C58.3 38.8 53 39.5 53.5 42.2 C56 44.5 59 44.3 60.8 42.6 Z" fill="#5FD39A" />
        </g>

        <!-- 摊成小麻薯的身体 -->
        <path
          d="M60 48.5 C76 48.5 85 56 85 66.5 C85 75.5 76.5 79.5 60 79.5 C43.5 79.5 35 75.5 35 66.5 C35 56 44 48.5 60 48.5 Z"
          fill="#18181B"
        />
        <!-- 垫在下巴旁的两只小爪 -->
        <ellipse cx="41.5" cy="75.5" rx="5.2" ry="3" fill="#18181B" />
        <ellipse cx="78.5" cy="75.5" rx="5.2" ry="3" fill="#18181B" />

        <!-- 弯弯的熟睡眼 -->
        <path d="M47.5 65 Q52.5 69.2 57.5 65" stroke="#FFFFFF" stroke-width="2.2" stroke-linecap="round" />
        <path d="M62.5 65 Q67.5 69.2 72.5 65" stroke="#FFFFFF" stroke-width="2.2" stroke-linecap="round" />
        <!-- 小呼噜嘴 -->
        <path d="M58.2 70.5 Q60 72.2 61.8 70.5" stroke="#FFFFFF" stroke-width="1.6" stroke-linecap="round" opacity="0.72" />
      </g>

      <!-- 缓缓飘起的 Zzz -->
      <g class="ws-zzz-1">
        <path
          d="M84.5 41.5 H90 L84.5 47 H90"
          stroke="#969BF2"
          stroke-width="1.8"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </g>
      <g class="ws-zzz-2">
        <path
          d="M93 29 H100.5 L93 36.5 H100.5"
          stroke="#6265DC"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </g>
    </g>
  </svg>
</template>

<style scoped>
.ws-dash {
  animation: ws-dash-flow 2.8s linear infinite;
}
.ws-bob {
  animation: ws-bob 3.6s ease-in-out infinite;
}
.ws-peek {
  animation: ws-peek 4.2s ease-in-out infinite;
}
.ws-plane {
  animation: ws-plane 3.8s ease-in-out infinite;
}
.ws-breathe {
  transform-origin: 60px 79px;
  animation: ws-breathe 3.8s ease-in-out infinite;
}
.ws-blink {
  transform-box: fill-box;
  transform-origin: center;
  animation: ws-blink 5.2s ease-in-out infinite;
}
.ws-twinkle {
  transform-box: fill-box;
  transform-origin: center;
  animation: ws-twinkle 2.6s ease-in-out infinite;
}
.ws-zzz-1 {
  animation: ws-zzz 3.2s ease-in-out infinite;
}
.ws-zzz-2 {
  animation: ws-zzz 3.2s ease-in-out 0.9s infinite;
}

@keyframes ws-dash-flow {
  to { stroke-dashoffset: -16; }
}
@keyframes ws-bob {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-2px); }
}
@keyframes ws-peek {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-2.5px); }
}
@keyframes ws-plane {
  0%, 100% { transform: translate(0, 0); }
  50% { transform: translate(2.5px, -2px); }
}
@keyframes ws-breathe {
  0%, 100% { transform: scaleY(1); }
  50% { transform: scaleY(1.035) scaleX(0.99); }
}
@keyframes ws-blink {
  0%, 92%, 100% { transform: scaleY(1); }
  96% { transform: scaleY(0.12); }
}
@keyframes ws-twinkle {
  0%, 100% { opacity: 0.72; transform: scale(0.92); }
  50% { opacity: 1; transform: scale(1.08); }
}
@keyframes ws-zzz {
  0%, 100% { opacity: 0.45; transform: translateY(2px); }
  50% { opacity: 1; transform: translateY(-2.5px); }
}

@media (prefers-reduced-motion: reduce) {
  .ws-dash,
  .ws-bob,
  .ws-peek,
  .ws-plane,
  .ws-breathe,
  .ws-blink,
  .ws-twinkle,
  .ws-zzz-1,
  .ws-zzz-2 {
    animation: none;
  }
}
</style>

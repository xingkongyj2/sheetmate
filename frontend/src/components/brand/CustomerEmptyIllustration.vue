<script setup lang="ts">
/**
 * 客户详情页无数据空态插画：与工作区面板保持同一套原创「小墨墨（小团子）」IP 视觉语言。
 * 覆盖 4 个空态场景：处理逻辑（logic）、流程（recipes）、输入文件（files）、输出文件（outputs）。
 */
withDefaults(defineProps<{
  kind: 'logic' | 'recipes' | 'files' | 'outputs' | 'memory' | 'mapping'
  size?: 'md' | 'sm'
}>(), { size: 'md' })
</script>

<template>
  <svg
    class="mo-empty-scene overflow-visible select-none"
    :class="size === 'sm' ? 'h-[92px] w-[124px]' : 'h-[108px] w-[144px]'"
    viewBox="0 0 144 108"
    fill="none"
    aria-hidden="true"
    focusable="false"
  >
    <!-- ========== 场景 1：处理逻辑（翻开空白说明卡片、好奇思索的小团子） ========== -->
    <g v-if="kind === 'logic'">
      <!-- 柔和暖纸背景衬底与装饰点 -->
      <ellipse cx="72" cy="60" rx="54" ry="34" fill="#F4F3EE" />
      <circle cx="23" cy="33" r="2.4" fill="#DCDAF8" />
      <circle cx="122" cy="76" r="2.6" fill="#E6E4DC" />
      <circle cx="117" cy="24" r="2" fill="#C5C8FA" />

      <!-- 右侧漂浮的空白处理逻辑卡 -->
      <g class="mo-float">
        <rect x="86" y="18" width="42" height="50" rx="9.5" fill="#FFFFFF" stroke="#E2E0D9" stroke-width="1.5" />
        <rect x="94" y="27" width="18" height="3.8" rx="1.9" fill="#6265DC" opacity="0.88" />
        <rect x="94" y="35.5" width="26" height="3" rx="1.5" fill="#ECEAE3" />
        <rect x="94" y="42.5" width="21" height="3" rx="1.5" fill="#ECEAE3" />
        <rect
          x="94"
          y="50.5"
          width="26"
          height="9"
          rx="4"
          fill="#F6F7FE"
          stroke="#B8BCF5"
          stroke-width="1.2"
          stroke-dasharray="3 2.5"
        />
      </g>

      <!-- 地面投影 -->
      <ellipse cx="58" cy="95.5" rx="24" ry="3.6" fill="#18181B" opacity="0.08" />

      <!-- 小团子（小墨墨）本体 -->
      <g class="mo-bob">
        <!-- 招牌小绿芽 -->
        <path d="M58 46.5 C58 42.2 58.6 38.8 59.8 35.4" stroke="#159A5D" stroke-width="2.2" stroke-linecap="round" />
        <path d="M59.8 37.2 C63.2 31.2 71.5 32.3 71 36.8 C67 40.4 62 40.2 59.8 37.2 Z" fill="#22B573" />
        <path d="M59 39.2 C56.1 34.6 49.8 35.5 50.4 38.8 C53.4 41.5 57 41.3 59 39.2 Z" fill="#5FD39A" />

        <!-- 左右小手（右手举着小火花向说明卡片示意） -->
        <ellipse cx="35.5" cy="75" rx="4.4" ry="6.4" fill="#18181B" transform="rotate(-24 35.5 75)" />
        <ellipse cx="80.5" cy="65.5" rx="4.4" ry="6.6" fill="#18181B" transform="rotate(38 80.5 65.5)" />

        <!-- 圆团子身体 -->
        <path
          d="M58 45.5 C73.5 45.5 83 56 83 70.5 C83 83 73.5 89.5 58 89.5 C42.5 89.5 33 83 33 70.5 C33 56 42.5 45.5 58 45.5 Z"
          fill="#18181B"
        />

        <!-- 好奇望向空白卡片的大眼睛 -->
        <g class="mo-blink">
          <ellipse cx="50.5" cy="67" rx="6" ry="7.2" fill="#FFFFFF" />
          <ellipse cx="65.5" cy="67" rx="6" ry="7.2" fill="#FFFFFF" />
          <circle cx="52.6" cy="65.4" r="3.1" fill="#18181B" />
          <circle cx="67.6" cy="65.4" r="3.1" fill="#18181B" />
          <circle cx="53.9" cy="63.9" r="1.15" fill="#FFFFFF" />
          <circle cx="68.9" cy="63.9" r="1.15" fill="#FFFFFF" />
        </g>

        <!-- 小思考嘴 -->
        <path d="M55.5 77.2 Q58 79.4 60.5 77.2" stroke="#FFFFFF" stroke-width="1.6" stroke-linecap="round" opacity="0.72" />

        <!-- 漂浮的靛蓝 + 暖金小星火 -->
        <path
          class="mo-twinkle"
          d="M78 44 L79.7 48.6 L84.3 50.3 L79.7 52 L78 56.6 L76.3 52 L71.7 50.3 L76.3 48.6 Z"
          fill="#6265DC"
        />
        <path
          class="mo-twinkle-delay"
          d="M31 46 L32.2 49.1 L35.3 50.3 L32.2 51.5 L31 54.6 L29.8 51.5 L26.7 50.3 L29.8 49.1 Z"
          fill="#969BF2"
        />
      </g>
    </g>

    <!-- ========== 场景 2：流程（举着节点火花、看着待连线虚线槽的小团子） ========== -->
    <g v-else-if="kind === 'recipes'">
      <ellipse cx="72" cy="58" rx="54" ry="34" fill="#F4F3EE" />
      <circle cx="25" cy="24" r="2.4" fill="#DCDAF8" />
      <circle cx="121" cy="76" r="2.8" fill="#E6E4DC" />
      <circle cx="124" cy="24" r="2" fill="#C5C8FA" />

      <!-- 流动虚线连接路径 -->
      <path
        class="mo-dash"
        d="M55 44 C67 44 71 32 85 32"
        stroke="#969BF2"
        stroke-width="2.3"
        stroke-linecap="round"
        stroke-dasharray="4.5 4.5"
      />

      <!-- 左侧已有流程节点卡片 -->
      <rect x="16" y="30" width="39" height="28" rx="8.5" fill="#FFFFFF" stroke="#E3E1DA" stroke-width="1.5" />
      <circle cx="25.5" cy="39.5" r="3.1" fill="#6265DC" />
      <rect x="32" y="37.5" width="15.5" height="3.6" rx="1.8" fill="#D4D6FA" />
      <rect x="23" y="47.5" width="24" height="3.3" rx="1.65" fill="#ECEAE3" />

      <!-- 右侧虚线空节点槽 -->
      <rect
        x="86"
        y="18"
        width="39"
        height="28"
        rx="8.5"
        fill="#F6F7FE"
        stroke="#969BF2"
        stroke-width="1.6"
        stroke-dasharray="4.5 3.5"
      />
      <path d="M105.5 27.2 v9.6 M100.7 32 h9.6" stroke="#6265DC" stroke-width="2" stroke-linecap="round" />

      <!-- 地面投影 -->
      <ellipse cx="71" cy="96" rx="23" ry="3.6" fill="#18181B" opacity="0.08" />

      <!-- 小团子（小墨墨）本体 -->
      <g class="mo-bob">
        <path d="M71 51 C71 46.8 71.6 43.5 72.8 40.2" stroke="#159A5D" stroke-width="2.2" stroke-linecap="round" />
        <path d="M72.8 41.8 C76.2 35.8 84.5 36.8 84 41.4 C80 45 75 44.8 72.8 41.8 Z" fill="#22B573" />
        <path d="M72 43.8 C69 39.2 62.8 40 63.4 43.4 C66.4 46 70 45.8 72 43.8 Z" fill="#5FD39A" />

        <ellipse cx="49.5" cy="78" rx="4.3" ry="6.2" fill="#18181B" transform="rotate(-24 49.5 78)" />
        <ellipse cx="93.5" cy="69.5" rx="4.3" ry="6.5" fill="#18181B" transform="rotate(38 93.5 69.5)" />

        <path
          d="M71 50 C86 50 95 60 95 74 C95 86 86 92.5 71 92.5 C56 92.5 47 86 47 74 C47 60 56 50 71 50 Z"
          fill="#18181B"
        />

        <g class="mo-blink">
          <ellipse cx="63.5" cy="70.5" rx="5.9" ry="7" fill="#FFFFFF" />
          <ellipse cx="78.5" cy="70.5" rx="5.9" ry="7" fill="#FFFFFF" />
          <circle cx="65.4" cy="68.7" r="3" fill="#18181B" />
          <circle cx="80.4" cy="68.7" r="3" fill="#18181B" />
          <circle cx="66.6" cy="67.2" r="1.15" fill="#FFFFFF" />
          <circle cx="81.6" cy="67.2" r="1.15" fill="#FFFFFF" />
        </g>

        <circle cx="71" cy="80.2" r="1.5" fill="#FFFFFF" opacity="0.72" />

        <path
          class="mo-twinkle"
          d="M104 54 L105.8 59 L110.8 60.8 L105.8 62.6 L104 67.6 L102.2 62.6 L97.2 60.8 L102.2 59 Z"
          fill="#6265DC"
        />
      </g>
    </g>

    <!-- ========== 场景 3：输入文件（空文件夹里探出头、双爪扒在边缘张望的小团子） ========== -->
    <g v-else-if="kind === 'files'">
      <!-- 漂浮纸飞机与虚线航迹 -->
      <g class="mo-plane">
        <path d="M93 29 C99 23.5 106 22 111 18.5" stroke="#C5C8FA" stroke-width="1.7" stroke-linecap="round" stroke-dasharray="3 3" />
        <path d="M108 14.5 L125 8.5 L117.5 23.5 L114.2 18 L108 14.5 Z" fill="#6265DC" />
        <path d="M125 8.5 L114.2 18 L113.4 23 L117.5 20" fill="#969BF2" />
      </g>
      <circle cx="24" cy="26" r="2.4" fill="#E6E4DC" />
      <circle cx="122" cy="43" r="2.2" fill="#DCDAF8" />

      <!-- 地面投影 -->
      <ellipse cx="71" cy="96" rx="40" ry="3.8" fill="#18181B" opacity="0.08" />

      <!-- 文件夹背板 -->
      <path
        d="M28 34 A7.5 7.5 0 0 1 35.5 26.5 H53.5 A7 7 0 0 1 58.8 29 L62.5 33 H105.5 A7.5 7.5 0 0 1 113 40.5 V84 A9 9 0 0 1 104 93 H37 A9 9 0 0 1 28 84 Z"
        fill="#C5C8FA"
      />
      <!-- 文件夹内部暗槽 -->
      <rect x="33" y="38" width="75" height="40" rx="7" fill="#8C91EC" />

      <!-- 从文件夹口袋探出头的小团子 -->
      <g class="mo-peek">
        <path d="M71 29 C71 24.8 71.6 21.5 72.8 18.2" stroke="#159A5D" stroke-width="2.2" stroke-linecap="round" />
        <path d="M72.8 19.8 C76.2 14 84.5 15 84 19.5 C80 23 75 22.8 72.8 19.8 Z" fill="#22B573" />
        <path d="M72 21.8 C69 17.4 62.8 18.2 63.4 21.4 C66.4 24 70 23.8 72 21.8 Z" fill="#5FD39A" />

        <path
          d="M71 28.5 C85.5 28.5 94.5 37.5 94.5 51 V62 H47.5 V51 C47.5 37.5 56.5 28.5 71 28.5 Z"
          fill="#18181B"
        />

        <g class="mo-blink">
          <ellipse cx="63.5" cy="44.5" rx="5.8" ry="6.8" fill="#FFFFFF" />
          <ellipse cx="78.5" cy="44.5" rx="5.8" ry="6.8" fill="#FFFFFF" />
          <circle cx="64.8" cy="43.7" r="3" fill="#18181B" />
          <circle cx="79.8" cy="43.7" r="3" fill="#18181B" />
          <circle cx="66" cy="42.2" r="1.1" fill="#FFFFFF" />
          <circle cx="81" cy="42.2" r="1.1" fill="#FFFFFF" />
        </g>
      </g>

      <!-- 文件夹前盖板 -->
      <path
        d="M24 59 A7.5 7.5 0 0 1 31.5 52 H110.5 A7.5 7.5 0 0 1 118 59 L114 85.5 A9 9 0 0 1 105.1 93.5 H36.9 A9 9 0 0 1 28 85.5 Z"
        fill="#FFFFFF"
        stroke="#DFDDD6"
        stroke-width="1.6"
      />
      <rect x="38" y="67.5" width="21" height="3.6" rx="1.8" fill="#E3E5FC" />
      <rect x="38" y="75" width="13" height="3.3" rx="1.65" fill="#F0EFEA" />
      <circle cx="101" cy="76.5" r="4" fill="#F4F3EE" />
      <circle cx="101" cy="76.5" r="1.8" fill="#969BF2" />

      <!-- 扒在边缘的一对小黑爪 -->
      <ellipse cx="53" cy="52.5" rx="6" ry="3.8" fill="#18181B" />
      <ellipse cx="89" cy="52.5" rx="6" ry="3.8" fill="#18181B" />
    </g>

    <!-- ========== 场景 4：输出文件（端着空托盘、笑眯眯等待出单的小团子） ========== -->
    <g v-else-if="kind === 'outputs'">
      <ellipse cx="72" cy="60" rx="54" ry="34" fill="#F4F3EE" />
      <circle cx="24" cy="42" r="2.4" fill="#E6E4DC" />
      <path
        class="mo-twinkle"
        d="M111 26 L112.8 30.8 L117.6 32.6 L112.8 34.4 L111 39.2 L109.2 34.4 L104.4 32.6 L109.2 30.8 Z"
        fill="#6265DC"
      />
      <path
        class="mo-twinkle-delay"
        d="M126 39 L127.1 41.8 L129.9 42.9 L127.1 44 L126 46.8 L124.9 44 L122.1 42.9 L124.9 41.8 Z"
        fill="#969BF2"
      />

      <!-- 地面投影 -->
      <ellipse cx="56" cy="95.5" rx="23" ry="3.6" fill="#18181B" opacity="0.08" />

      <!-- 小团子（小墨墨）本体 -->
      <g class="mo-bob">
        <path d="M56 47 C56 42.8 56.6 39.5 57.8 36.2" stroke="#159A5D" stroke-width="2.2" stroke-linecap="round" />
        <path d="M57.8 37.8 C61.2 31.8 69.5 32.8 69 37.4 C65 41 60 40.8 57.8 37.8 Z" fill="#22B573" />
        <path d="M57 39.8 C54 35.2 47.8 36 48.4 39.4 C51.4 42 55 41.8 57 39.8 Z" fill="#5FD39A" />

        <!-- 左手自然放下，右手稳稳托起交付托盘 -->
        <ellipse cx="34.5" cy="75.5" rx="4.2" ry="6.2" fill="#18181B" transform="rotate(-22 34.5 75.5)" />
        <ellipse cx="81" cy="68" rx="6.2" ry="4" fill="#18181B" transform="rotate(-14 81 68)" />

        <!-- 交付小托盘与待生成虚线罩 -->
        <g class="mo-float">
          <path
            d="M84 62 H128 L124 68.5 H88 Z"
            fill="#FFFFFF"
            stroke="#DFDDD6"
            stroke-width="1.6"
            stroke-linejoin="round"
          />
          <path
            d="M93.5 56 A13 13 0 0 1 118.5 56"
            stroke="#969BF2"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-dasharray="3.5 3.5"
          />
          <circle cx="106" cy="52" r="2.8" fill="#6265DC" />
        </g>

        <!-- 圆团子身体 -->
        <path
          d="M56 46 C71 46 80 56 80 70 C80 82.5 71 89.5 56 89.5 C41 89.5 32 82.5 32 70 C32 56 41 46 56 46 Z"
          fill="#18181B"
        />

        <!-- 期待的大眼睛与小微笑 -->
        <g class="mo-blink">
          <ellipse cx="48.5" cy="67" rx="5.8" ry="6.9" fill="#FFFFFF" />
          <ellipse cx="63.5" cy="67" rx="5.8" ry="6.9" fill="#FFFFFF" />
          <circle cx="50.5" cy="66" r="3" fill="#18181B" />
          <circle cx="65.5" cy="66" r="3" fill="#18181B" />
          <circle cx="51.7" cy="64.5" r="1.15" fill="#FFFFFF" />
          <circle cx="66.7" cy="64.5" r="1.15" fill="#FFFFFF" />
        </g>
        <path d="M53.2 76.8 Q56 79.6 58.8 76.8" stroke="#FFFFFF" stroke-width="1.7" stroke-linecap="round" opacity="0.78" />
      </g>
    </g>

    <!-- ========== 场景 5：记忆（头顶记忆星火、仰望空白便利贴的小团子） ========== -->
    <g v-else-if="kind === 'memory'">
      <ellipse cx="72" cy="59" rx="52" ry="33" fill="#F4F3EE" />
      <circle cx="24" cy="36" r="2.3" fill="#DCDAF8" />
      <circle cx="122" cy="73" r="2.4" fill="#E6E4DC" />

      <!-- 漂浮的淡紫折角便签卡 -->
      <g class="mo-float">
        <path
          d="M86 22 A7 7 0 0 1 93 15 H119 A7 7 0 0 1 126 22 V44 L117 53 H93 A7 7 0 0 1 86 46 Z"
          fill="#FFFFFF"
          stroke="#E2E0D9"
          stroke-width="1.5"
          stroke-linejoin="round"
        />
        <path d="M117 53 V46.5 A2.5 2.5 0 0 1 119.5 44 H126 Z" fill="#E6E7FC" stroke="#C5C8FA" stroke-width="1.2" stroke-linejoin="round" />
        <rect x="93" y="24" width="16" height="3.2" rx="1.6" fill="#6265DC" opacity="0.85" />
        <rect x="93" y="31.5" width="24" height="2.8" rx="1.4" fill="#ECEAE3" />
        <rect x="93" y="38" width="18" height="2.8" rx="1.4" fill="#ECEAE3" />
      </g>

      <!-- 记忆星火 -->
      <path
        class="mo-twinkle"
        d="M36 27 L37.7 31.6 L42.3 33.3 L37.7 35 L36 39.6 L34.3 35 L29.7 33.3 L34.3 31.6 Z"
        fill="#6265DC"
      />
      <path
        class="mo-twinkle-delay"
        d="M78 21 L79 23.8 L81.8 24.8 L79 25.8 L78 28.6 L77 25.8 L74.2 24.8 L77 23.8 Z"
        fill="#969BF2"
      />

      <!-- 地面投影 -->
      <ellipse cx="60" cy="95.5" rx="23" ry="3.5" fill="#18181B" opacity="0.08" />

      <!-- 小团子本体 -->
      <g class="mo-bob">
        <path d="M60 47.5 C60 43.2 60.6 40 61.8 36.6" stroke="#159A5D" stroke-width="2.2" stroke-linecap="round" />
        <path d="M61.8 38.2 C65.2 32.2 73.5 33.2 73 37.8 C69 41.4 64 41.2 61.8 38.2 Z" fill="#22B573" />
        <path d="M61 40.2 C58 35.6 51.8 36.4 52.4 39.8 C55.4 42.4 59 42.2 61 40.2 Z" fill="#5FD39A" />

        <ellipse cx="38.5" cy="75.5" rx="4.2" ry="6.2" fill="#18181B" transform="rotate(-24 38.5 75.5)" />
        <ellipse cx="81.5" cy="75.5" rx="4.2" ry="6.2" fill="#18181B" transform="rotate(24 81.5 75.5)" />

        <path
          d="M60 46.5 C75 46.5 84 56.5 84 70.5 C84 82.8 75 89.5 60 89.5 C45 89.5 36 82.8 36 70.5 C36 56.5 45 46.5 60 46.5 Z"
          fill="#18181B"
        />

        <g class="mo-blink">
          <ellipse cx="52.5" cy="67" rx="5.8" ry="7" fill="#FFFFFF" />
          <ellipse cx="67.5" cy="67" rx="5.8" ry="7" fill="#FFFFFF" />
          <circle cx="54.4" cy="65.2" r="3" fill="#18181B" />
          <circle cx="69.4" cy="65.2" r="3" fill="#18181B" />
          <circle cx="55.6" cy="63.7" r="1.15" fill="#FFFFFF" />
          <circle cx="70.6" cy="63.7" r="1.15" fill="#FFFFFF" />
        </g>
        <path d="M57.4 77 Q60 79.4 62.6 77" stroke="#FFFFFF" stroke-width="1.6" stroke-linecap="round" opacity="0.74" />
      </g>
    </g>

    <!-- ========== 场景 6：字段映射（中间托举左右两枚字段胶囊连线的小团子） ========== -->
    <g v-else>
      <ellipse cx="72" cy="59" rx="54" ry="33" fill="#F4F3EE" />
      <circle cx="23" cy="70" r="2.2" fill="#E6E4DC" />
      <circle cx="122" cy="24" r="2.2" fill="#DCDAF8" />

      <!-- 上方左右两张待配对字段标签 + 流动虚线桥 -->
      <path
        class="mo-dash"
        d="M54 31 H90"
        stroke="#969BF2"
        stroke-width="2.2"
        stroke-linecap="round"
        stroke-dasharray="4 4"
      />
      <path d="M85.5 27.2 L89.8 31 L85.5 34.8" stroke="#6265DC" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />

      <g class="mo-float">
        <rect x="16" y="20" width="38" height="22" rx="7.5" fill="#FFFFFF" stroke="#E2E0D9" stroke-width="1.5" />
        <rect x="23" y="28.5" width="24" height="5" rx="2.5" fill="#6265DC" opacity="0.88" />
      </g>
      <g class="mo-float">
        <rect x="90" y="20" width="38" height="22" rx="7.5" fill="#F6F7FE" stroke="#969BF2" stroke-width="1.5" stroke-dasharray="3.5 3" />
        <rect x="97" y="28.5" width="24" height="5" rx="2.5" fill="#C5C8FA" />
      </g>

      <!-- 地面投影 -->
      <ellipse cx="72" cy="96" rx="23" ry="3.5" fill="#18181B" opacity="0.08" />

      <!-- 小团子本体 -->
      <g class="mo-bob">
        <path d="M72 49.5 C72 45.2 72.6 42 73.8 38.6" stroke="#159A5D" stroke-width="2.2" stroke-linecap="round" />
        <path d="M73.8 40.2 C77.2 34.2 85.5 35.2 85 39.8 C81 43.4 76 43.2 73.8 40.2 Z" fill="#22B573" />
        <path d="M73 42.2 C70 37.6 63.8 38.4 64.4 41.8 C67.4 44.4 71 44.2 73 42.2 Z" fill="#5FD39A" />

        <!-- 双手微微扬起托着上面的配对线 -->
        <ellipse cx="49" cy="68.5" rx="4.2" ry="6.2" fill="#18181B" transform="rotate(-38 49 68.5)" />
        <ellipse cx="95" cy="68.5" rx="4.2" ry="6.2" fill="#18181B" transform="rotate(38 95 68.5)" />

        <path
          d="M72 48.5 C87 48.5 96 58.5 96 72.5 C96 84.5 87 91 72 91 C57 91 48 84.5 48 72.5 C48 58.5 57 48.5 72 48.5 Z"
          fill="#18181B"
        />

        <g class="mo-blink">
          <ellipse cx="64.5" cy="69" rx="5.8" ry="6.9" fill="#FFFFFF" />
          <ellipse cx="79.5" cy="69" rx="5.8" ry="6.9" fill="#FFFFFF" />
          <circle cx="64.5" cy="67.2" r="3" fill="#18181B" />
          <circle cx="79.5" cy="67.2" r="3" fill="#18181B" />
          <circle cx="65.7" cy="65.7" r="1.15" fill="#FFFFFF" />
          <circle cx="80.7" cy="65.7" r="1.15" fill="#FFFFFF" />
        </g>
        <path d="M69.2 78.5 Q72 81.2 74.8 78.5" stroke="#FFFFFF" stroke-width="1.6" stroke-linecap="round" opacity="0.76" />
      </g>
    </g>
  </svg>
</template>

<style scoped>
.mo-dash {
  animation: mo-dash-flow 2.8s linear infinite;
}
.mo-bob {
  animation: mo-bob 3.6s ease-in-out infinite;
}
.mo-float {
  animation: mo-float 4s ease-in-out infinite;
}
.mo-peek {
  animation: mo-peek 4.2s ease-in-out infinite;
}
.mo-plane {
  animation: mo-plane 3.8s ease-in-out infinite;
}
.mo-blink {
  transform-box: fill-box;
  transform-origin: center;
  animation: mo-blink 5.2s ease-in-out infinite;
}
.mo-twinkle {
  transform-box: fill-box;
  transform-origin: center;
  animation: mo-twinkle 2.6s ease-in-out infinite;
}
.mo-twinkle-delay {
  transform-box: fill-box;
  transform-origin: center;
  animation: mo-twinkle 2.6s ease-in-out 1s infinite;
}

@keyframes mo-dash-flow {
  to { stroke-dashoffset: -18; }
}
@keyframes mo-bob {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-2.2px); }
}
@keyframes mo-float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-2.8px); }
}
@keyframes mo-peek {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-2.6px); }
}
@keyframes mo-plane {
  0%, 100% { transform: translate(0, 0); }
  50% { transform: translate(2.5px, -2px); }
}
@keyframes mo-blink {
  0%, 92%, 100% { transform: scaleY(1); }
  96% { transform: scaleY(0.12); }
}
@keyframes mo-twinkle {
  0%, 100% { opacity: 0.7; transform: scale(0.92); }
  50% { opacity: 1; transform: scale(1.08); }
}

@media (prefers-reduced-motion: reduce) {
  .mo-dash,
  .mo-bob,
  .mo-float,
  .mo-peek,
  .mo-plane,
  .mo-blink,
  .mo-twinkle,
  .mo-twinkle-delay {
    animation: none;
  }
}
</style>

<script setup lang="ts">
/** 文件预览页：在新的标签页里整页展示一个文件（表格或图片）。刷新、复制地址都停在这个文件。 */
import { CircleAlert, LoaderCircle } from '@lucide/vue'
import { defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fileApi } from '@/api'
import { errorText } from '@/api/http'
import type { FileInfo } from '@/api/types'

const WorkbookPreview = defineAsyncComponent(() => import('@/features/workbook-preview/WorkbookPreview.vue'))
const route = useRoute()
const router = useRouter()
const file = ref<FileInfo>()
const localFile = ref<File>()
const error = ref('')

async function load() {
  error.value = ''
  try {
    if (route.params.fileId === 'local') {
      // 本地文件：只接受本站自己生成的 blob 地址
      const source = String(route.query.blob ?? '')
      if (!source.startsWith(`blob:${location.origin}/`)) throw new Error('这个本地文件的预览地址无效，请回到原页面重新点击查看。')
      let blob: Blob
      try {
        blob = await (await fetch(source)).blob()
      } catch {
        throw new Error('本地文件已无法读取，请回到原页面重新点击查看。')
      }
      localFile.value = new File([blob], String(route.query.name || '文件'), { type: blob.type })
    } else file.value = await fileApi.get(String(route.params.fileId))
  } catch (err) {
    error.value = errorText(err)
  }
}

/** 关闭这个标签页；直接在地址栏打开的页面浏览器不让脚本关，退回首页。 */
function close() {
  window.close()
  setTimeout(() => { if (!window.closed) void router.replace({ name: 'home' }) }, 150)
}

onMounted(load)
</script>

<template>
  <WorkbookPreview v-if="file || localFile" :file="file" :local-file="localFile" page @close="close" />
  <div v-else-if="error" class="flex h-dvh flex-col items-center justify-center gap-4 px-6 text-center">
    <CircleAlert class="size-8 text-mute" />
    <p role="alert" class="max-w-lg text-sm leading-6 text-mute">{{ error }}</p>
    <button type="button" class="btn-line" @click="load">重新加载</button>
  </div>
  <div v-else class="flex h-dvh items-center justify-center" role="status"><LoaderCircle class="size-7 animate-spin text-mute" /></div>
</template>

import { useRouter } from 'vue-router'

import type { FileInfo } from '@/api/types'

/**
 * 查看文件：统一在新的浏览器标签页里整页打开（/preview/文件ID），不再在当前页面上盖弹层。
 *
 * 已上传的文件按文件 ID 打开；还没上传的本地文件（首页刚选中的附件）没有 ID，
 * 把它做成 blob 地址交给新标签页读取（同一个浏览器、同一来源，原标签页开着就能读）。
 */
export function useFilePreview() {
  const router = useRouter()
  return function openFilePreview(source: FileInfo | File | string) {
    const target = source instanceof File
      ? router.resolve({ name: 'preview', params: { fileId: 'local' }, query: { blob: URL.createObjectURL(source), name: source.name } })
      : router.resolve({ name: 'preview', params: { fileId: typeof source === 'string' ? source : source.file_id } })
    window.open(target.href, '_blank')
  }
}

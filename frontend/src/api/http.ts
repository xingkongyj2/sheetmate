/**
 * axios 实例：统一前缀、带 Cookie、把后端的 {ok:false, error:{message}} 转成 ApiError。
 */
import axios, { AxiosError } from 'axios'

export class ApiError extends Error {
  status: number
  type: string

  constructor(message: string, status = 0, type = 'error') {
    super(message)
    this.status = status
    this.type = type
  }
}

export const http = axios.create({ baseURL: '/api', withCredentials: true, timeout: 120_000 })

/** 未登录时的回调（由 router 注册，跳转登录页） */
let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

http.interceptors.response.use(
  (resp) => {
    const data = resp.data
    if (data && typeof data === 'object' && data.ok === false) {
      return Promise.reject(new ApiError(data.error?.message ?? '请求失败', resp.status, data.error?.type))
    }
    return resp
  },
  async (err: AxiosError<{ error?: { message?: string; type?: string } }>) => {
    const status = err.response?.status ?? 0
    let body: unknown = err.response?.data
    // 以 blob 下载文件时，出错的 JSON 也会被包成 Blob：先转成文本，才读得到后端写给用户看的原因
    if (typeof Blob !== 'undefined' && body instanceof Blob) {
      try { body = JSON.parse(await body.text()) } catch { body = undefined }
    }
    const info = (body as { error?: { message?: string; type?: string } } | undefined)?.error
    const message = info?.message ?? (status ? `请求失败（${status}）` : '连接不上服务，请确认后端已经启动')
    if (status === 401 && onUnauthorized) onUnauthorized()
    return Promise.reject(new ApiError(message, status, info?.type))
  },
)

export function errorText(err: unknown): string {
  if (err instanceof Error) return err.message
  return String(err)
}

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
  (err: AxiosError<{ error?: { message?: string; type?: string } }>) => {
    const status = err.response?.status ?? 0
    const message = err.response?.data?.error?.message ?? (status ? `请求失败（${status}）` : '连接不上服务，请确认后端已经启动')
    if (status === 401 && onUnauthorized) onUnauthorized()
    return Promise.reject(new ApiError(message, status, err.response?.data?.error?.type))
  },
)

export function errorText(err: unknown): string {
  if (err instanceof Error) return err.message
  return String(err)
}

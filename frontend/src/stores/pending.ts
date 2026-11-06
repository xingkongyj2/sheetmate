/**
 * 从首页发起对话时，先建会话、传文件，再跳到对话页；要发送的第一条消息暂存在这里，对话页接手发送。
 */
export interface PendingStart {
  message: string
  plan: boolean
  attachments: string[]
}

export const pendingStarts = new Map<string, PendingStart>()

/**
 * 从首页发起对话时，先建会话、传文件，再跳到对话页；要发送的第一条消息暂存在这里，对话页接手发送。
 */
export interface PendingStart {
  message: string
  plan: boolean
  attachments: string[]
}

export const pendingStarts = new Map<string, PendingStart>()

/** 首页的示例需求 */
export const SUGGESTIONS: { icon: string; text: string; mode: 'analyze' | 'modify' }[] = [
  { icon: 'sigma', text: '按客户统计运费合计和票数，从高到低排', mode: 'analyze' },
  { icon: 'blocks', text: '一个运单有多行费用，把运费、保价费、包装费拆成列', mode: 'modify' },
  { icon: 'magnet', text: '用店铺名称匹配归属表，匹配不到的记为「新食研」', mode: 'modify' },
  { icon: 'pie', text: '对比这两个月的账单，找出金额变化最大的店铺', mode: 'analyze' },
]

import type { FileInfo, RecipeJSON } from '@/api/types'
import type { FileArtifact } from './chatModel'

export interface FlowFileNode {
  key: string
  direction: 'input' | 'output'
  name: string
  aliases: string[]
  pattern?: string
  fileId?: string
  source?: string
  sources?: string[]
}

export type FlowSelection = { kind: 'step'; id: number } | { kind: 'file'; file: FlowFileNode }

/** Resource identity travels with a flow so every entry opens the same viewer. */
export interface RecipeFlowContext {
  title?: string
  recipeId?: string | null
  version?: number
  customerId?: string | null
  conversationId?: string | null
  runId?: string | null
  recipe?: RecipeJSON
  files?: FileInfo[]
  resultFiles?: FileArtifact[]
  running?: boolean
  failed?: boolean
  deleted?: boolean
}

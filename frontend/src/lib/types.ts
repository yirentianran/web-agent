export interface Message {
  type: 'user' | 'assistant' | 'system' | 'tool_use' | 'tool_result' | 'result' | 'error' | 'stream_event' | 'file_upload' | 'file_result' | 'heartbeat'
  content: string
  subtype?: string
  name?: string
  index: number
  replay?: boolean
  is_error?: boolean  // for tool_result error flag
  cost_usd?: number
  total_cost_usd?: number
  duration_ms?: number
  usage?: Record<string, number>
  id?: string       // for tool_use blocks
  input?: unknown   // for tool_use blocks
  data?: unknown    // for system messages, file_result
  uuid?: string     // for stream_event
  event?: unknown   // for stream_event
  session_id?: string
  state?: string    // for session_state_changed system messages
  user_id?: string  // for file_result download URLs
  // Hook execution tracking
  hook_id?: string  // unique ID for hook execution
  hook_name?: string // name of the hook (e.g., "SessionStart:startup")
  hook_event?: string // event type (e.g., "SessionStart")
  outcome?: string  // "success" or "error" for hook_response
}

export interface SessionItem {
  session_id: string
  title: string
  last_message?: string
  status: 'idle' | 'running' | 'completed' | 'error' | 'waiting_user' | 'cancelled'
  cost_usd?: number
  created_at?: string
  size_mb?: number
  last_active_at?: string
  file_count?: number
}

export interface SessionState {
  state: 'idle' | 'running' | 'completed' | 'error' | 'waiting_user' | 'cancelled'
  cost_usd: number
  last_active: number
}

/** Payload of the AskUserQuestion tool — streamed to the frontend as tool_use.input */
export interface AskUserQuestionInput {
  questions: Array<{
    header: string
    question: string
    options: Array<{ label: string; description?: string }>
  }>
}

export type SkillSource = 'shared' | 'personal'

export interface Skill {
  name: string
  source: SkillSource
  description: string
  content: string
  path: string
  created_at: string
  created_by: string
}

export interface TodoWriteTodo {
  content: string
  status: 'pending' | 'in_progress' | 'completed' | 'deleted'
  activeForm?: string
}

export interface TodoWriteInput {
  todos: TodoWriteTodo[]
}

export type McpServerType = 'stdio' | 'http'

export interface McpServer {
  name: string
  type: McpServerType
  command?: string
  args?: string[]
  url?: string
  env?: Record<string, string>
  tools: string[]
  description: string
  enabled: boolean
}

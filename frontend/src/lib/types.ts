export interface Message {
  type: 'user' | 'assistant' | 'system' | 'tool_use' | 'tool_result' | 'result' | 'error' | 'stream_event' | 'file_upload' | 'file_result' | 'heartbeat' | 'auth_error'
  content: string
  message?: string  // error messages from backend
  subtype?: string
  name?: string
  index: number
  replay?: boolean
  is_error?: boolean  // for tool_result error flag
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
  // Client-generated message ID for dedup (UUID v4)
  clientMsgId?: string
  // Send state for optimistic user messages
  sendState?: MessageSendState
  // Heartbeat: whether the backend agent task is still running
  agent_alive?: boolean
}

/** Send state machine for user messages */
export type MessageSendState = 'sending' | 'failed'

/** Session lifecycle status */
export type SessionStatus = 'idle' | 'running' | 'completed' | 'error' | 'waiting_user' | 'cancelled'

/** WebSocket connection status */
export type ConnectionStatus = 'connected' | 'connecting' | 'reconnecting' | 'failed'

export interface SessionItem {
  session_id: string
  title: string
  last_message?: string
  status: SessionStatus
  created_at?: string
  size_mb?: number
  last_active_at?: string
  file_count?: number
}

/** Payload of the AskUserQuestion tool — streamed to the frontend as tool_use.input */
export interface AskUserQuestionInput {
  questions: Array<{
    header: string
    question: string
    options: Array<{ label: string; description?: string }>
  }>
}

type SkillSource = 'shared' | 'personal'

export interface Skill {
  name: string
  source: SkillSource
  owner: string
  description: string
  content: string
  path: string
  created_at: string
  created_by: string
  valid: boolean
}

export interface TodoWriteTodo {
  content: string
  status: 'pending' | 'in_progress' | 'completed' | 'deleted'
  activeForm?: string
}

export type McpServerType = 'stdio' | 'http' | 'sse' | 'streamable_http'

export interface McpResource {
  uri: string
  name: string
  description: string
  mimeType?: string
}

export interface McpPromptArgument {
  name: string
  description?: string
  required?: boolean
}

export interface McpPrompt {
  name: string
  description: string
  arguments?: McpPromptArgument[]
}

export interface McpServer {
  name: string
  type: McpServerType
  command?: string
  args?: string[]
  url?: string
  headers?: Record<string, string>
  env?: Record<string, string>
  tools: string[]
  resources: McpResource[]
  prompts: McpPrompt[]
  description: string
  enabled: boolean
  discoverStatus?: 'connected' | 'disconnected'
  discoverError?: string
}

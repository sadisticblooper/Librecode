// TypeScript type definitions for opencode UI

export interface Model {
  id: string;
  label: string;
  ctx: number;
}

export interface Agent {
  id: string;
  name: string;
  description: string;
}

export interface ChatMessage {
  id?: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  reasoning_content?: string;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  _pending?: boolean;
  _partial?: boolean;
  _compaction?: boolean;
}

export interface ToolCall {
  id: string;
  type: 'function';
  function: {
    name: string;
    arguments: string;
  };
}

export interface Chat {
  id: string;
  title: string;
  workingDirs: string[];
  history: ChatMessage[];
  createdAt: number;
}

export interface StreamEvent {
  type: 'text' | 'thinking' | 'tool_use' | 'tool_delta' | 'tool_done' | 'done' | 'error' | 'heartbeat' | 'history_update' | 'subagent_start' | 'subagent_stream' | 'subagent_done' | 'compaction';
  text?: string;
  name?: string;
  args?: Record<string, unknown>;
  tc_id?: string;
  key?: string;
  agent?: string;
  task?: string;
  context?: string;
  subtype?: string;
  data?: string;
  result?: string;
  history?: ChatMessage[];
}

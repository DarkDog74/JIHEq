export type AuditStatus =
  | 'passed'
  | 'failed'
  | 'not_applicable'

export type AiNarrationStatus =
  | 'pending'
  | 'streaming'
  | 'completed'
  | 'failed'
  | 'skipped'

export interface AiNarration {
  status: AiNarrationStatus | string
  model?: string
  text?: string
  cached?: boolean
  error_type?: string
  message?: string
  error_code?: string
  error_message?: string
  generated_at?: string
  input_sha256?: string
  prompt_version?: string
  usage?: Record<string, unknown>
}

export interface ExtractedSummaryEntry {
  field: string
  value: string
}

export type AiStreamEventType =
  | 'ai_started'
  | 'ai_delta'
  | 'ai_completed'
  | 'ai_failed'

export interface AiStreamEvent {
  type: AiStreamEventType
  item_id: string
  item_name?: string
  current?: number
  total?: number
  chunk_seq?: number
  event_id?: string | number
  audit_status?: AuditStatus
  conclusion?: string
  extracted_summary?: ExtractedSummaryEntry[]
  delta?: string
  narration?: AiNarration
  error_type?: string
  message?: string
  timestamp?: string
}

export type AuditProgressEventType =
  | 'audit_started'
  | 'audit_complete'

export interface AuditProgressEvent {
  type: AuditProgressEventType
  item_id: string
  item_name?: string
  current?: number
  total?: number
  status?: AuditStatus | string
  reason_code?: string
  ai_pending?: boolean
  ai_narration?: AiNarration
  message?: string
  event_id?: string | number
  timestamp?: string
}

export interface PreviewDocument {
  name: string
  relative_path: string
  category_code: string
  category_name: string
  size: number
}

export interface PreviewCategoryStatus {
  category_code: string
  category_name: string
  count: number
  missing: boolean
}

export interface SourcePreview {
  source_path: string
  system_name: string
  department: string
  system_id: string
  recognized_count: number
  category_counts: Record<string, number>
  expected_category_count: number
  present_category_count: number
  missing_categories: string[]
  category_statuses: PreviewCategoryStatus[]
  documents: PreviewDocument[]
  has_previous_result: boolean
}

export interface Job {
  id: string
  system_id: string
  source_path: string
  status: string
  stage: string
  progress: number
  message: string
  error?: string
}

export interface AuditItem {
  item_id: string
  item_name: string
  status: AuditStatus
  reason_code?: string
  message: string
  source_documents: string[]
  issues: string[]
  extracted_summary: ExtractedSummaryEntry[]
  fields_file?: string | null
  result_file?: string | null
  ai_narration?: AiNarration | null
}

export interface AuditDetail {
  summary?: AuditItem
  fields?: Record<string, unknown> | null
  result?: Record<string, unknown> | null
  ai_narration?: AiNarration | null
  narration?: AiNarration | null
  error?: string
}

export interface AuditSummary {
  generated_at: string
  system: {
    id: string
    name: string
    department: string
    source_path: string
  }
  parse: {
    exit_code: number
    file_count: number
    recognized_count: number
    category_counts: Record<string, number>
    failed_file_count: number
    failed_files: Record<string, unknown>[]
  }
  audit: {
    item_count: number
    counts: Record<string, number>
    items: AuditItem[]
  }
}

export interface AuthPreset {
  key: string
  label: string
  auth_header: string
  auth_scheme: string
  chat_path: string
  stream: boolean
}

export interface ModelConfig {
  base_url: string
  api_key_set: boolean
  api_key_masked: string
  model: string
  suggested_models: string[]
  timeout_seconds: number
  auth_header: string
  auth_scheme: string
  chat_path: string
  stream: boolean
  max_tokens: number
  /** 原样合并进请求体的 JSON 对象文本；非空时平台不再自动加思考参数 */
  extra_body: string
  /** 原样合并进请求头的 JSON 对象文本 */
  extra_headers: string
  extra_body_active: boolean
  auth_presets: AuthPreset[]
  /** 后端按当前配置拼出的完整请求地址，直接展示，省得靠猜 */
  endpoint_preview: string
  configured: boolean
}

export interface ModelConfigForm {
  base_url: string
  api_key: string
  model: string
  timeout_seconds: number
  auth_header: string
  auth_scheme: string
  chat_path: string
  stream: boolean
  max_tokens: number
  extra_body: string
  extra_headers: string
}

export interface ModelTestResult {
  ok: boolean
  latency_ms: number | null
  model: string
  endpoint?: string
  message: string
}

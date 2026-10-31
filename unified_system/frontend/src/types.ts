export interface User {
  id: string
  email: string
  display_name: string
  status: 'active' | 'disabled'
  roles: string[]
  created_at: string
  last_login_at: string | null
}

export interface Platform {
  key: string
  name: string
  code_available: boolean
  integration_status: string
  integrated: boolean
  auth_methods: string[]
  capabilities: string[]
  last_verified_at: string | null
  status_note: string
}

export interface Account {
  id: string
  platform_key: string
  platform_name: string
  label: string
  account_hint: string
  status: string
  account_mode: 'personal' | 'shared'
  created_at: string
  updated_at?: string
  platform_last_verified_at?: string | null
  available?: boolean
  grant_id?: string
  access_scope?: { task_kinds?: string[] }
  grant_expires_at?: string | null
  available_from?: string | null
  expires_at?: string | null
  is_open?: boolean
  max_concurrency?: number
  grants?: Grant[]
}

export interface Grant {
  id: string
  user_id: string
  user_email: string
  valid_from: string
  expires_at: string | null
  revoked_at: string | null
  scope: { task_kinds?: string[] }
}

export interface Pagination {
  page: number
  page_size: number
  total: number
  pages: number
}

export interface SessionInfo {
  id: string
  created_at: string
  last_seen_at: string
  expires_at: string
  user_agent: string
  current: boolean
}

export interface AdminSummary {
  scope: string
  service: { api: string; database: string }
  users: { total: number; active: number; disabled: number }
  invitations: null | { total: number; usable: number; exhausted: number; revoked: number }
  shared_accounts: { total: number; open: number; closed: number }
  platform_accounts: { key: string; name: string; account_count: number }[]
  task_statuses: Record<string, number>
  current_tasks_by_platform: Record<string, number>
  workers: null | { total: number; online: number; offline: number }
  recent_task_errors: { task_id: string; platform_key: string; worker_id: string | null; error_code: string; occurred_at: string }[]
  recent_operations: { id: string; actor_user_id: string | null; target_user_id: string | null; action: string; entity_type: string; entity_id: string; created_at: string }[]
}

export interface ApiErrorBody {
  error?: { message?: string; status?: number }
}

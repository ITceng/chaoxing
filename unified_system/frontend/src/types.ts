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

export interface Course {
  id: string
  account_id: string
  account_mode: 'personal' | 'shared'
  platform_key: string
  course_id: string
  course_name: string
  teacher_name: string
  semester: string
  resource_status: string
  source_verified: boolean
  last_synced_at: string
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
  verified_at?: string | null
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

export interface OperationalAlert {
  id: string
  code: string
  severity: 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
  state: 'active' | 'resolved'
  title: string
  details: Record<string, string | number | boolean | null>
  first_seen_at: string | null
  last_seen_at: string | null
  last_notified_at: string | null
  resolved_at: string | null
  occurrence_count: number
  notification_failures: number
}

export interface OpsOverview {
  generated_at: string
  monitor: { ops_check_state: string; ops_check_at: string | null }
  host: {
    state: string; captured_at: string | null; age_seconds: number | null
    cpu_percent: number | null; memory_percent: number | null; load_1m: number | null
    root_disk: { total_bytes: number | null; free_bytes: number | null; used_percent: number | null }
    backup_disk: { total_bytes: number | null; free_bytes: number | null; used_percent: number | null }
  }
  services: Record<string, { state: string; instances: number | null; resources: { cpu_percent: number | null; memory_used_bytes: number | null; memory_limit_bytes: number | null; memory_percent: number | null; pids_total: number | null } }>
  readiness: { state: string; http_status: number | null; latency_ms: number | null }
  backup: { state: string; latest_at: string | null; archive_bytes: number | null; age_seconds: number | null; retained_count: number | null; service_state: string; service_exit_status: number | null; service_finished_at: string | null }
  certificate: { state: string; days_remaining: number | null }
  tasks: { statuses: Record<string, number>; queued: number; oldest_queued_minutes: number | null; failed_last_15m: number }
  workers: {
    state: string; total: number; online: number; offline: number; active_platforms_without_worker: number
    platforms: { key: string; name: string; integration_status: string; worker_count: number; online_workers: number; state: string; last_heartbeat_at: string | null }[]
  }
  ai_usage: {
    state: string; enabled_providers: number; records_last_24h: number
    input_tokens_last_24h: number | null; output_tokens_last_24h: number | null; failures_last_10m: number
  }
  alerts: { active: OperationalAlert[]; recently_resolved: OperationalAlert[] }
}

export interface ApiErrorBody {
  error?: { message?: string; status?: number }
}

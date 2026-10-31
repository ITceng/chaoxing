export function dateTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export function shortId(value?: string | null): string {
  return value ? `${value.slice(0, 8)}…` : '—'
}

export function errorMessage(error: any): string {
  return error?.response?.data?.error?.message || error?.message || '操作失败，请稍后重试'
}

export function platformInitial(key: string): string {
  return ({ chaoxing: '超', zhihuishu: '知', yinghua: '英' } as Record<string, string>)[key] || '学'
}

export function statusText(status?: string): string {
  const labels: Record<string, string> = {
    pending: '待接入', active: '已验证', unverified: '未验证', disabled: '已禁用', expired: '已过期',
    error: '异常', offline: '离线', online: '在线', queued: '排队中', leased: '已领取', running: '运行中',
    cancel_requested: '等待取消', succeeded: '已完成', failed: '失败', cancelled: '已取消', timed_out: '已超时',
  }
  return labels[status || ''] || status || '未知'
}

export function statusClass(status?: string): string {
  if (['active', 'succeeded', 'online'].includes(status || '')) return 'status-ok'
  if (['pending', 'unverified', 'queued', 'leased', 'running', 'cancel_requested'].includes(status || '')) return 'status-pending'
  if (['disabled', 'expired', 'offline', 'cancelled'].includes(status || '')) return 'status-off'
  if (['failed', 'timed_out', 'error'].includes(status || '')) return 'status-danger'
  return 'status-blue'
}

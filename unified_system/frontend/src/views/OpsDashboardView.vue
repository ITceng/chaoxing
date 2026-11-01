<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Connection, Cpu, DataAnalysis, Monitor, Refresh, Timer } from '@element-plus/icons-vue'
import api from '../services/api'
import type { OperationalAlert, OpsOverview } from '../types'
import { dateTime, errorMessage, statusClass } from '../utils/format'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const loading = ref(true)
const error = ref('')
const overview = ref<OpsOverview | null>(null)
const activeAlerts = computed(() => overview.value?.alerts.active || [])
const resolvedAlerts = computed(() => overview.value?.alerts.recently_resolved || [])

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await api.get<OpsOverview>('/admin/ops/overview')
    overview.value = data
  } catch (err) {
    error.value = errorMessage(err)
    ElMessage.error(error.value)
  } finally {
    loading.value = false
  }
}

function textOrUnknown(value: string | null | undefined, suffix = ''): string {
  return value === null || value === undefined || value === '' ? '未知' : `${value}${suffix}`
}
function percent(value: number | null | undefined): string { return value === null || value === undefined ? '未知' : `${value.toFixed(1)}%` }
function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '未知'
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(0)} MB`
  return `${(value / 1024 ** 3).toFixed(2)} GB`
}
function duration(value: number | null | undefined): string {
  if (value === null || value === undefined) return '未知'
  if (value < 3600) return `${Math.floor(value / 60)} 分钟`
  return `${Math.floor(value / 3600)} 小时 ${Math.floor(value % 3600 / 60)} 分钟`
}
function serviceName(key: string): string {
  return ({ nginx: 'Host Nginx', postgres: 'PostgreSQL', backend: 'API 后端', edge: '前端边缘代理',
    'worker-chaoxing': '超星 Worker', 'worker-zhihuishu': '智慧树 Worker', 'worker-yinghua': '英华 Worker' } as Record<string, string>)[key] || key
}
function serviceText(state?: string): string {
  return ({ running: '运行中', starting: '启动中', active: '运行中', unhealthy: '异常', inactive: '未运行', failed: '失败',
    not_deployed: '未部署', unknown: '未知', ready: '就绪', not_ready: '未就绪',
    available: '有备份元数据', missing: '无备份', valid: '有效', expired: '已过期',
    online: '在线', offline: '离线', degraded: '异常', not_integrated: '未接入',
    ok: '正常', pending: '待执行', initializing: '初始化中' } as Record<string, string>)[state || ''] || '未知'
}
function severityType(severity: OperationalAlert['severity']): 'info' | 'warning' | 'danger' | 'success' {
  return severity === 'CRITICAL' || severity === 'ERROR' ? 'danger' : severity === 'WARNING' ? 'warning' : 'info'
}
function severityText(severity: OperationalAlert['severity']): string { return severity }
function detailText(value: string | number | boolean | null): string { return value === null ? '未知' : String(value) }

onMounted(load)
</script>

<template>
  <div class="page-wrap ops-dashboard">
    <PageHeader eyebrow="OPERATIONS / REAL SIGNALS" title="运维监控" description="此页只呈现后端、主机、数据库、任务与 Worker 的可观测状态，不提供凭据解密、备份下载、恢复或密钥操作。">
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新监控</el-button>
    </PageHeader>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="ops-error" />

    <div v-loading="loading" class="ops-data">
      <template v-if="overview">
        <div class="scope-banner ops-banner">
          <div><strong>数据状态：{{ serviceText(overview.host.state === 'ok' ? 'ok' : overview.host.state) }}</strong>
            <span>最近主机快照：{{ dateTime(overview.host.captured_at) }} · 年龄 {{ duration(overview.host.age_seconds) }}</span>
          </div>
          <div class="health-items">
            <span class="health-item"><i class="pulse-dot"></i>就绪探针 {{ serviceText(overview.readiness.state) }}</span>
            <span class="health-item"><i class="pulse-dot"></i>告警检查 {{ serviceText(overview.monitor.ops_check_state) }}</span>
          </div>
        </div>

        <div class="metric-grid ops-metrics">
          <article class="surface metric-card"><div class="metric-top">主机 CPU<span class="metric-icon"><el-icon><Cpu /></el-icon></span></div><div class="metric-value">{{ percent(overview.host.cpu_percent) }}</div><div class="metric-foot">最近一次主机采样；非进程级指标</div></article>
          <article class="surface metric-card"><div class="metric-top">内存使用<span class="metric-icon"><el-icon><DataAnalysis /></el-icon></span></div><div class="metric-value">{{ percent(overview.host.memory_percent) }}</div><div class="metric-foot">系统 MemAvailable / MemTotal</div></article>
          <article class="surface metric-card"><div class="metric-top">根分区使用<span class="metric-icon"><el-icon><Monitor /></el-icon></span></div><div class="metric-value">{{ percent(overview.host.root_disk.used_percent) }}</div><div class="metric-foot">可用 {{ bytes(overview.host.root_disk.free_bytes) }}</div></article>
          <article class="surface metric-card"><div class="metric-top">任务队列<span class="metric-icon"><el-icon><Timer /></el-icon></span></div><div class="metric-value">{{ overview.tasks.queued }}</div><div class="metric-foot">最久等待 {{ overview.tasks.oldest_queued_minutes === null ? '未知' : `${overview.tasks.oldest_queued_minutes} 分钟` }} · 15 分钟失败 {{ overview.tasks.failed_last_15m }}</div></article>
          <article class="surface metric-card"><div class="metric-top">Worker 心跳<span class="metric-icon"><el-icon><Connection /></el-icon></span></div><div class="metric-value">{{ overview.workers.state === 'not_integrated' ? '—' : `${overview.workers.online} / ${overview.workers.total}` }}</div><div class="metric-foot">{{ overview.workers.state === 'not_integrated' ? '暂无已启用的平台接入，不将缺失 Worker 标记为在线' : `活跃平台无新鲜心跳：${overview.workers.active_platforms_without_worker}` }}</div></article>
          <article class="surface metric-card"><div class="metric-top">AI Token 用量<span class="metric-icon"><el-icon><DataAnalysis /></el-icon></span></div><div class="metric-value">{{ overview.ai_usage.state === 'measured' ? (overview.ai_usage.input_tokens_last_24h! + overview.ai_usage.output_tokens_last_24h!).toLocaleString() : '—' }}</div><div class="metric-foot">{{ overview.ai_usage.state === 'measured' ? `24 小时记录值 · 输入 ${overview.ai_usage.input_tokens_last_24h!.toLocaleString()} / 输出 ${overview.ai_usage.output_tokens_last_24h!.toLocaleString()}` : overview.ai_usage.state === 'not_integrated' ? 'AI provider 未启用；用量不可测' : '尚无用量记录；不能推断为零' }}</div></article>
        </div>

        <div class="content-grid equal ops-grid">
          <section class="surface surface-pad">
            <div class="surface-title"><div><h2>服务状态</h2><p>Docker Compose 与 systemd 状态；CPU / 内存 / PID 来自 docker stats，各服务实例聚合，未知值不补零。</p></div></div>
            <div class="ops-service-list">
              <div v-for="(service, key) in overview.services" :key="key" class="ops-service-row">
                <span class="ops-service-name">{{ serviceName(String(key)) }}</span>
                <span class="status-pill" :class="statusClass(service.state)">{{ serviceText(service.state) }}</span>
                <span class="ops-service-count">{{ service.instances === null ? '实例数未知' : `${service.instances} 个实例` }} · CPU {{ percent(service.resources.cpu_percent) }} · 内存 {{ bytes(service.resources.memory_used_bytes) }} / {{ bytes(service.resources.memory_limit_bytes) }} · PID {{ textOrUnknown(service.resources.pids_total?.toString()) }}</span>
              </div>
            </div>
            <div class="notice-strip ops-note">HTTP 探针耗时：{{ textOrUnknown(overview.readiness.latency_ms === null ? null : overview.readiness.latency_ms.toFixed(1), ' ms') }}；一般 API 的 5xx 比例 / p95 未持久化，因此不展示估算值。</div>
          </section>

          <section class="surface surface-pad">
            <div class="surface-title"><div><h2>备份与 TLS</h2><p>仅展示本机侧车清单、文件大小与运行状态；不会触碰或下载备份。</p></div></div>
            <div class="ops-facts">
              <div><span>最近备份清单</span><strong>{{ overview.backup.latest_at ? dateTime(overview.backup.latest_at) : serviceText(overview.backup.state) }}</strong></div>
              <div><span>距今</span><strong>{{ duration(overview.backup.age_seconds) }}</strong></div>
              <div><span>归档文件大小</span><strong>{{ bytes(overview.backup.archive_bytes) }}</strong></div>
              <div><span>保留清单数</span><strong>{{ textOrUnknown(overview.backup.retained_count?.toString()) }}</strong></div>
              <div><span>备份分区使用</span><strong>{{ percent(overview.host.backup_disk.used_percent) }} · 可用 {{ bytes(overview.host.backup_disk.free_bytes) }}</strong></div>
              <div><span>TLS 证书剩余</span><strong>{{ overview.certificate.days_remaining === null ? '未知' : `${overview.certificate.days_remaining} 天` }}</strong></div>
              <div><span>备份服务最近状态</span><strong>{{ serviceText(overview.backup.service_state) }} · {{ dateTime(overview.backup.service_finished_at) }}</strong></div>
            </div>
            <div class="notice-strip warning ops-note">清单与文件存在且大小匹配，不等同于本次全量 SHA-256 重验或恢复演练；实际 PostgreSQL 恢复状态以隔离恢复报告为准。</div>
          </section>
        </div>

        <section class="surface surface-pad ops-platforms">
          <div class="surface-title"><div><h2>平台 Worker 心跳</h2><p>在线只按数据库心跳与 TTL 判定；未接入平台保持“未接入”。</p></div></div>
          <el-table :data="overview.workers.platforms" class="data-table" row-key="key">
            <el-table-column label="平台" prop="name" min-width="160" />
            <el-table-column label="接入状态" min-width="120"><template #default="{row}"><span class="status-pill" :class="statusClass(row.integration_status)">{{ serviceText(row.integration_status) }}</span></template></el-table-column>
            <el-table-column label="Worker" min-width="110"><template #default="{row}">{{ row.integration_status === 'active' ? `${row.online_workers} 在线 / ${row.worker_count} 已登记` : '未知 / 未接入' }}</template></el-table-column>
            <el-table-column label="最近心跳" min-width="175"><template #default="{row}">{{ dateTime(row.last_heartbeat_at) }}</template></el-table-column>
            <template #empty><EmptyState title="平台目录暂无记录" description="这里只显示数据库中的平台配置与真实心跳。" /></template>
          </el-table>
        </section>

        <section class="surface surface-pad ops-alerts">
          <div class="surface-title"><div><h2>告警与恢复记录</h2><p>活动告警按指纹去重并应用冷却；条件解除后记录恢复。级别：INFO / WARNING / ERROR / CRITICAL。</p></div><el-tag v-if="activeAlerts.length" type="danger" effect="light">{{ activeAlerts.length }} 条活动告警</el-tag></div>
          <el-tabs>
            <el-tab-pane label="活动告警">
              <el-table :data="activeAlerts" class="data-table" row-key="id">
                <el-table-column label="级别" width="125"><template #default="{row}"><el-tag :type="severityType(row.severity)">{{ severityText(row.severity) }}</el-tag></template></el-table-column>
                <el-table-column label="事件" min-width="260"><template #default="{row}"><strong>{{ row.title }}</strong><div class="table-secondary">{{ row.code }} · 出现 {{ row.occurrence_count }} 次</div></template></el-table-column>
                <el-table-column label="观测详情" min-width="200"><template #default="{row}"><span v-for="(value,key) in row.details" :key="key" class="ops-detail">{{ key }}: {{ detailText(value) }}</span><span v-if="!Object.keys(row.details).length" class="muted">无附加详情</span></template></el-table-column>
                <el-table-column label="首次发现" min-width="160"><template #default="{row}">{{ dateTime(row.first_seen_at) }}</template></el-table-column>
                <el-table-column label="最近通知" min-width="160"><template #default="{row}">{{ dateTime(row.last_notified_at) }}<span v-if="row.notification_failures" class="table-secondary">通知失败 {{ row.notification_failures }} 次</span></template></el-table-column>
                <template #empty><EmptyState title="当前没有活动告警" description="仅在实际测量到阈值或服务状态异常时创建告警。" /></template>
              </el-table>
            </el-tab-pane>
            <el-tab-pane label="最近恢复">
              <el-table :data="resolvedAlerts" class="data-table" row-key="id">
                <el-table-column label="级别" width="125"><template #default="{row}"><el-tag :type="severityType(row.severity)">{{ severityText(row.severity) }}</el-tag></template></el-table-column>
                <el-table-column label="事件" min-width="260"><template #default="{row}"><strong>{{ row.title }}</strong><div class="table-secondary">{{ row.code }}</div></template></el-table-column>
                <el-table-column label="恢复时间" min-width="170"><template #default="{row}">{{ dateTime(row.resolved_at) }}</template></el-table-column>
                <template #empty><EmptyState title="暂无恢复记录" description="历史恢复事件会在此显示。" /></template>
              </el-table>
            </el-tab-pane>
          </el-tabs>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.ops-banner{margin-bottom:15px}.ops-metrics{grid-template-columns:repeat(3,minmax(0,1fr))}.ops-grid,.ops-platforms,.ops-alerts{margin-top:15px}.ops-service-list{display:grid}.ops-service-row{display:grid;grid-template-columns:minmax(110px,1fr) auto minmax(90px,.65fr);align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid #eef2f3}.ops-service-row:last-child{border-bottom:0}.ops-service-name{font-size:10px;font-weight:650;color:#425965}.ops-service-count{font-size:9px;text-align:right;color:#87969d}.ops-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.ops-facts div{padding:10px;border-radius:9px;background:#f8fafb}.ops-facts span,.ops-facts strong{display:block}.ops-facts span{font-size:9px;color:#84939a}.ops-facts strong{margin-top:5px;color:#435965;font-size:11px;line-height:1.5}.ops-note{margin-top:13px}.ops-detail{display:inline-block;margin:0 8px 3px 0;color:#71828a;font-size:9px}.ops-alerts :deep(.el-tabs__content){overflow:visible}.ops-error{margin-bottom:12px}@media(max-width:900px){.ops-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:520px){.ops-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.ops-service-row{grid-template-columns:1fr auto}.ops-service-count{grid-column:1/-1;text-align:left}.ops-facts{grid-template-columns:1fr}}
</style>

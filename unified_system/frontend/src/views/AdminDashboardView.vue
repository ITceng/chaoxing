<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Connection, DataAnalysis, Files, Refresh, Timer, User, UserFilled } from '@element-plus/icons-vue'
import api from '../services/api'
import type { AdminSummary } from '../types'
import { dateTime, errorMessage, statusClass, statusText } from '../utils/format'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const router=useRouter()
const loading=ref(true)
const error=ref('')
const summary=ref<AdminSummary|null>(null)
const taskActive=computed(()=>['queued','leased','running','cancel_requested'].reduce((total,key)=>total+(summary.value?.task_statuses?.[key]||0),0))
const maxPlatformCount=computed(()=>Math.max(1,...(summary.value?.platform_accounts||[]).map(p=>p.account_count)))
async function load(){loading.value=true;error.value='';try{const {data}=await api.get<AdminSummary>('/admin/dashboard');summary.value=data}catch(err){error.value=errorMessage(err)}finally{loading.value=false}}
onMounted(load)
</script>

<template>
  <div class="page-wrap">
    <PageHeader eyebrow="ADMINISTRATION / OVERVIEW" title="管理概览" description="统计数据由后端按当前管理员权限范围计算，不会把不可见用户或模拟 Worker 计入结果。"><el-button :icon="Refresh" :loading="loading" @click="load">刷新数据</el-button></PageHeader>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="dashboard-alert" />
    <div v-loading="loading" class="admin-dashboard-data">
      <template v-if="summary">
        <div class="scope-banner"><div><strong>{{ summary.scope==='global'?'全局管理视图':'授权范围管理视图' }}</strong><span>{{ summary.scope==='global'?'统计涵盖系统全局数据。':'统计只包含当前管理员可管理的用户、账号和任务。' }}</span></div><div class="health-items"><span class="health-item"><i class="pulse-dot"></i>API {{ summary.service.api==='ok'?'正常':'异常' }}</span><span class="health-item"><i class="pulse-dot"></i>数据库 {{ summary.service.database==='ok'?'正常':'异常' }}</span></div></div>
        <div class="metric-grid admin-metrics">
          <article class="surface metric-card"><div class="metric-top">范围内用户<span class="metric-icon"><el-icon><User /></el-icon></span></div><div class="metric-value">{{ summary.users.total }}</div><div class="metric-foot">{{ summary.users.active }} 正常 · {{ summary.users.disabled }} 已禁用</div></article>
          <article class="surface metric-card"><div class="metric-top">共享账号<span class="metric-icon"><el-icon><Connection /></el-icon></span></div><div class="metric-value">{{ summary.shared_accounts.total }}</div><div class="metric-foot">{{ summary.shared_accounts.open }} 个开放 · {{ summary.shared_accounts.closed }} 个关闭</div></article>
          <article v-if="summary.invitations" class="surface metric-card"><div class="metric-top">有效邀请码<span class="metric-icon"><el-icon><UserFilled /></el-icon></span></div><div class="metric-value">{{ summary.invitations.usable }}</div><div class="metric-foot">共 {{ summary.invitations.total }} 个 · {{ summary.invitations.exhausted }} 个已用尽</div></article>
          <article v-else class="surface metric-card"><div class="metric-top">邀请码管理<span class="metric-icon"><el-icon><UserFilled /></el-icon></span></div><div class="metric-value">—</div><div class="metric-foot">当前权限范围不包含邀请码统计</div></article>
          <article class="surface metric-card"><div class="metric-top">当前任务<span class="metric-icon"><el-icon><Timer /></el-icon></span></div><div class="metric-value">{{ taskActive }}</div><div class="metric-foot">基于排队、领取和运行中的真实任务</div></article>
        </div>
        <div class="content-grid equal">
          <section class="surface surface-pad">
            <div class="surface-title"><div><h2>各平台账号数量</h2><p>超管为全局账号量；普通管理员仅计本人管理的共享账号和本人的个人账号。</p></div></div>
            <div v-if="summary.platform_accounts.length" class="platform-count-list"><div v-for="platform in summary.platform_accounts" :key="platform.key" class="platform-count"><div class="platform-count-name"><span>{{ platform.name }}</span><strong>{{ platform.account_count }}</strong></div><div class="count-track"><i :style="{width:`${Math.max(4,platform.account_count/maxPlatformCount*100)}%`}"></i></div></div></div>
            <EmptyState v-else title="暂无可统计账号" description="当前范围内还没有平台账号。" />
          </section>
          <section class="surface surface-pad">
            <div class="surface-title"><div><h2>服务与 Worker</h2><p>只展示健康探针、数据库连通结果和实际心跳登记。</p></div><el-button v-if="summary.workers" link type="primary" @click="router.push('/app/admin/workers')">Worker 详情</el-button></div>
            <div v-if="summary.workers" class="worker-summary"><div class="worker-stat"><span class="worker-dot online"></span><div><strong>{{ summary.workers.online }}</strong><small>在线 Worker</small></div></div><div class="worker-stat"><span class="worker-dot offline"></span><div><strong>{{ summary.workers.offline }}</strong><small>离线 Worker</small></div></div><div class="worker-stat"><span class="worker-dot task"></span><div><strong>{{ taskActive }}</strong><small>运行/排队任务</small></div></div></div>
            <div v-else class="notice-strip">当前管理员没有平台管理权限，Worker 总览不向此角色开放。</div>
            <div class="task-status-grid"><div v-for="status in ['queued','leased','running','succeeded','failed','timed_out']" :key="status" class="task-status-cell"><span>{{ statusText(status) }}</span><strong>{{ summary.task_statuses[status] || 0 }}</strong></div></div>
          </section>
        </div>
        <div class="content-grid equal admin-lower">
          <section class="surface surface-pad"><div class="surface-title"><div><h2>最近系统操作</h2><p>审计事件按当前权限范围脱敏。</p></div><el-button link type="primary" @click="router.push('/app/admin/audit')">查看日志</el-button></div>
            <div v-if="summary.recent_operations.length" class="activity-list"><div v-for="log in summary.recent_operations" :key="log.id" class="activity-row"><div class="activity-dot"><el-icon><Files /></el-icon></div><div class="activity-copy"><strong>{{ log.action }}</strong><span>{{ log.entity_type }} · {{ log.entity_id || '—' }} · {{ dateTime(log.created_at) }}</span></div></div></div><EmptyState v-else title="暂无操作记录" description="后端还没有产生可见的审计事件。" />
          </section>
          <section class="surface surface-pad"><div class="surface-title"><div><h2>最近任务异常</h2><p>来源于真实 failed / timed_out 任务，不代表 Worker 当前在线状态。</p></div></div>
            <div v-if="summary.recent_task_errors.length" class="activity-list"><div v-for="issue in summary.recent_task_errors" :key="issue.task_id" class="activity-row"><div class="activity-dot error-icon">!</div><div class="activity-copy"><strong>{{ issue.platform_key }} · {{ issue.error_code }}</strong><span>任务 {{ issue.task_id.slice(0,8) }} · Worker {{ issue.worker_id || '未分配' }} · {{ dateTime(issue.occurred_at) }}</span></div></div></div><EmptyState v-else title="暂无任务异常" description="当前数据范围内没有失败或超时任务。" />
          </section>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>.dashboard-alert{margin-bottom:14px}.admin-dashboard-data{min-height:240px}.scope-banner{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:13px 16px;margin-bottom:14px;border:1px solid #e4ecee;background:#fff;border-radius:12px}.scope-banner strong,.scope-banner span{display:block}.scope-banner strong{font-size:10px;color:#415864}.scope-banner span{margin-top:4px;color:#919ea5;font-size:9px}.health-items{display:flex;gap:8px}.health-item{display:flex;align-items:center;gap:7px;color:#4c766b;font-size:9px;padding:7px 9px;border-radius:8px;background:#eef7f3}.health-item .pulse-dot{width:6px;height:6px}.admin-metrics{grid-template-columns:repeat(4,minmax(0,1fr))}.platform-count-list{display:grid;gap:17px}.platform-count-name{display:flex;align-items:center;justify-content:space-between;font-size:10px;color:#60737e}.platform-count-name strong{color:#2e4855;font-size:12px}.count-track{height:6px;background:#f0f4f5;border-radius:99px;margin-top:7px;overflow:hidden}.count-track i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#1c8679,#83c9ad)}.worker-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.worker-stat{display:flex;align-items:center;gap:8px;padding:12px 9px;background:#f8fafb;border-radius:10px}.worker-stat strong,.worker-stat small{display:block}.worker-stat strong{font-size:16px;color:#334b58}.worker-stat small{margin-top:3px;font-size:8px;color:#929fa5}.worker-dot{width:8px;height:8px;flex:none;border-radius:50%;background:#b8c3c7}.worker-dot.online{background:#52ae88}.worker-dot.offline{background:#b7c1c5}.worker-dot.task{background:#d7a858}.task-status-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:14px}.task-status-cell{display:flex;justify-content:space-between;gap:4px;padding:8px 9px;border:1px solid #edf1f2;border-radius:8px;color:#89969d;font-size:8px}.task-status-cell strong{color:#4b606b}.admin-lower{margin-top:15px}.error-icon{background:#fff0ee;color:#b84b43;font-weight:700}@media(max-width:760px){.admin-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.scope-banner{align-items:flex-start;flex-direction:column}.health-items{width:100%}.health-item{flex:1;justify-content:center}}</style>

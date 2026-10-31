<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Connection, Monitor, Refresh, Warning } from '@element-plus/icons-vue'
import api from '../services/api'
import type { AdminSummary, Platform } from '../types'
import { dateTime, errorMessage, statusClass, statusText } from '../utils/format'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

interface Worker {worker_id:string;platform_key:string;status:'online'|'offline';version:string;capabilities:string[];last_seen_at:string|null}
const loading=ref(true);const error=ref('');const platforms=ref<Platform[]>([]);const workers=ref<Worker[]>([]);const summary=ref<AdminSummary|null>(null)
const activeCount=computed(()=>workers.value.filter(w=>w.status==='online').length)
const workerByPlatform=computed(()=>Object.fromEntries(platforms.value.map(p=>[p.key,workers.value.filter(w=>w.platform_key===p.key)])))
const currentTasks=computed(()=>summary.value?.current_tasks_by_platform||{})
async function load(){loading.value=true;error.value='';try{const [p,w,d]=await Promise.all([api.get<{platforms:Platform[]}>('/platforms'),api.get<{workers:Worker[]}>('/admin/platform-workers'),api.get<AdminSummary>('/admin/dashboard')]);platforms.value=p.data.platforms;workers.value=w.data.workers;summary.value=d.data}catch(err){error.value=errorMessage(err);ElMessage.error(error.value)}finally{loading.value=false}}
onMounted(load)
</script>

<template>
  <div class="page-wrap">
    <PageHeader eyebrow="PLATFORM OPERATIONS" title="平台与 Worker 状态" description="平台状态取自真实平台目录，Worker 在线状态按后端心跳 TTL 计算。没有心跳记录的平台会如实显示未接入。"><el-button :icon="Refresh" :loading="loading" @click="load">刷新心跳</el-button></PageHeader>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <div v-loading="loading">
      <div class="metric-grid worker-metrics"><article class="surface metric-card"><div class="metric-top">平台注册平台<span class="metric-icon"><el-icon><Connection /></el-icon></span></div><div class="metric-value">{{ platforms.length }}</div><div class="metric-foot">来自后端平台目录</div></article><article class="surface metric-card"><div class="metric-top">真实在线 Worker<span class="metric-icon"><el-icon><Monitor /></el-icon></span></div><div class="metric-value">{{ activeCount }}</div><div class="metric-foot">由最后心跳时间计算</div></article><article class="surface metric-card"><div class="metric-top">当前任务数<span class="metric-icon"><el-icon><Warning /></el-icon></span></div><div class="metric-value">{{ Object.values(currentTasks).reduce((a,b)=>a+b,0) }}</div><div class="metric-foot">按平台汇总的领取/运行任务</div></article></div>
      <div class="platform-grid worker-platforms"><article v-for="platform in platforms" :key="platform.key" class="surface platform-card"><div class="platform-top"><span class="platform-logo">{{ platform.key==='chaoxing'?'超':platform.key==='zhihuishu'?'知':'英' }}</span><span class="status-pill" :class="statusClass(platform.integration_status)">{{ statusText(platform.integration_status) }}</span></div><h3>{{ platform.name }}</h3><p>{{ platform.status_note||'后端没有提供状态说明。' }}</p><div class="worker-platform-meta"><div><span>Worker 数</span><strong>{{ workerByPlatform[platform.key]?.length||0 }}</strong></div><div><span>当前任务</span><strong>{{ currentTasks[platform.key]||0 }}</strong></div></div><div class="card-footer"><span>{{ workerByPlatform[platform.key]?.length?`${workerByPlatform[platform.key].filter(w=>w.status==='online').length} 在线`:'无 Worker 心跳记录' }}</span><span>{{ platform.code_available?'候选源码存在':'无已验证接入' }}</span></div></article></div>
      <section class="surface surface-pad worker-table-section"><div class="surface-title"><div><h2>Worker 心跳</h2><p>在线状态按后端配置的心跳超时计算；不会生成模拟在线数据。</p></div></div><el-table :data="workers" class="data-table" row-key="worker_id"><el-table-column label="Worker ID" prop="worker_id" min-width="180"/><el-table-column label="平台" prop="platform_key" min-width="130"/><el-table-column label="状态" width="110"><template #default="{row}"><span class="status-pill" :class="statusClass(row.status)">{{ statusText(row.status) }}</span></template></el-table-column><el-table-column label="版本" prop="version" min-width="120"><template #default="{row}">{{ row.version||'未提供' }}</template></el-table-column><el-table-column label="能力声明" min-width="180"><template #default="{row}"><el-tag v-for="cap in row.capabilities" :key="cap" size="small" effect="plain">{{ cap }}</el-tag><span v-if="!row.capabilities?.length" class="muted">未提供</span></template></el-table-column><el-table-column label="最近心跳" min-width="155"><template #default="{row}">{{ dateTime(row.last_seen_at) }}</template></el-table-column><template #empty><EmptyState title="尚未收到 Worker 心跳" description="三个平台当前均没有已接入的 Worker。上线 Worker 后，此处才会显示真实版本和心跳时间。" /></template></el-table></section>
      <section class="surface surface-pad worker-errors"><div class="surface-title"><div><h2>最近失败 / 超时任务</h2><p>后端目前提供任务错误码，不提供 Worker 进程异常日志或每 Worker 在途计数。</p></div></div><el-table :data="summary?.recent_task_errors||[]" class="data-table"><el-table-column label="平台" prop="platform_key" min-width="120"/><el-table-column label="错误码" prop="error_code" min-width="160"><template #default="{row}"><span class="status-pill status-danger">{{ row.error_code }}</span></template></el-table-column><el-table-column label="任务 ID" prop="task_id" min-width="180"/><el-table-column label="Worker" prop="worker_id" min-width="150"><template #default="{row}">{{ row.worker_id||'未分配' }}</template></el-table-column><el-table-column label="发生时间" min-width="160"><template #default="{row}">{{ dateTime(row.occurred_at) }}</template></el-table-column><template #empty><EmptyState title="暂无失败或超时任务" description="这里不展示推测的异常；只有后端真实记录的 failed / timed_out 任务才会出现。"/></template></el-table></section>
    </div>
  </div>
</template>

<style scoped>.worker-metrics{grid-template-columns:repeat(3,minmax(0,1fr))}.worker-platforms{margin-bottom:15px}.worker-platform-meta{display:flex;gap:25px;margin-top:15px}.worker-platform-meta span,.worker-platform-meta strong{display:block}.worker-platform-meta span{font-size:9px;color:#96a1a7}.worker-platform-meta strong{margin-top:4px;color:#344b56;font-size:15px}.worker-table-section,.worker-errors{margin-top:15px}@media(max-width:700px){.worker-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}</style>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Files, Search, CircleCheck } from '@element-plus/icons-vue'
import api from '../services/api'
import type { Pagination } from '../types'
import { dateTime, errorMessage } from '../utils/format'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

interface AuditRow{id:string;actor_user_id:string|null;actor_email:string;target_user_id:string|null;target_email:string;action:string;entity_type:string;entity_id:string;metadata:Record<string,unknown>;created_at:string}
interface LoginRow{id:string;user_id:string|null;identifier:string;result:string;user_agent:string;created_at:string}
const loading=ref(true);const error=ref('');const mode=ref<'audit'|'login'>('audit');const q=ref('');const action=ref('');const entity=ref('');const page=ref(1);const pageSize=ref(20);const pagination=ref<Pagination>({page:1,page_size:20,total:0,pages:0});const auditRows=ref<AuditRow[]>([]);const loginRows=ref<LoginRow[]>([]);const loginPage=ref(1);const loginPageSize=ref(20);const selected=ref<AuditRow|null>(null);const detailVisible=ref(false);const dateRange=ref<[string,string]|null>(null)
const loginFiltered=computed(()=>loginRows.value.filter(row=>!q.value.trim()||`${row.identifier} ${row.result} ${row.user_agent}`.toLowerCase().includes(q.value.trim().toLowerCase())))
const loginShown=computed(()=>loginFiltered.value.slice((loginPage.value-1)*loginPageSize.value,loginPage.value*loginPageSize.value))
let timer:ReturnType<typeof setTimeout>|undefined
watch([q,action,entity,dateRange],()=>{page.value=1;loginPage.value=1;clearTimeout(timer);timer=setTimeout(load,250)})
watch([page,pageSize],load)
watch(mode,()=>{q.value='';action.value='';entity.value='';dateRange.value=null;page.value=1;loginPage.value=1;load()})
async function load(){loading.value=true;error.value='';try{if(mode.value==='login'){const {data}=await api.get<{login_logs:LoginRow[]}>('/admin/login-logs');loginRows.value=data.login_logs}else{const params=new URLSearchParams({page:String(page.value),page_size:String(pageSize.value)});if(q.value.trim())params.set('q',q.value.trim());if(action.value.trim())params.set('action',action.value.trim());if(entity.value.trim())params.set('entity_type',entity.value.trim());if(dateRange.value){const start=new Date(`${dateRange.value[0]}T00:00:00`).toISOString();const end=new Date(`${dateRange.value[1]}T23:59:59`).toISOString();params.set('from',start);params.set('to',end)}const {data}=await api.get<{logs:AuditRow[];pagination:Pagination}>(`/admin/audit-logs?${params}`);auditRows.value=data.logs;pagination.value=data.pagination}}catch(err){error.value=errorMessage(err);ElMessage.error(error.value)}finally{loading.value=false}}
function showDetail(row:AuditRow){selected.value=row;detailVisible.value=true}
function outcomeText(result:string){return ({success:'成功',failure:'失败',throttled:'触发限流'} as Record<string,string>)[result]||result}
onMounted(load)
</script>

<template>
  <div class="page-wrap">
    <PageHeader eyebrow="SECURITY & COMPLIANCE" title="审计日志" description="系统操作日志经过脱敏，并由服务端按管理员权限范围过滤。登录成功、失败和限流记录可单独查看。" />
    <div class="audit-tabs"><button :class="{active:mode==='audit'}" @click="mode='audit'"><el-icon><Files /></el-icon>系统操作</button><button :class="{active:mode==='login'}" @click="mode='login'"><el-icon><CircleCheck /></el-icon>登录记录</button></div>
    <section class="surface surface-pad">
      <div class="table-toolbar"><div class="toolbar-filters"><el-input v-model="q" class="table-search" clearable :placeholder="mode==='audit'?'搜索操作、对象或实体 ID':'搜索邮箱、结果或浏览器'"><template #prefix><el-icon><Search /></el-icon></template></el-input><template v-if="mode==='audit'"><el-input v-model="action" class="action-filter" clearable placeholder="精确筛选动作"/><el-input v-model="entity" class="action-filter" clearable placeholder="实体类型"/><el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" start-placeholder="开始日期" end-placeholder="结束日期" range-separator="至"/></template></div><span class="muted text-small">{{ mode==='audit'?`${pagination.total} 条操作记录`:`${loginFiltered.length} 条登录记录` }}</span></div>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
      <el-table v-if="mode==='audit'" v-loading="loading" :data="auditRows" class="data-table" row-key="id">
        <el-table-column label="操作人" min-width="180"><template #default="{row}"><div class="table-primary">{{ row.actor_email }}</div><div class="table-secondary">{{ row.actor_user_id||'系统事件' }}</div></template></el-table-column>
        <el-table-column label="操作类型" min-width="180"><template #default="{row}"><el-tag size="small" effect="plain">{{ row.action }}</el-tag></template></el-table-column>
        <el-table-column label="操作对象" min-width="175"><template #default="{row}"><div class="table-primary">{{ row.target_email }}</div><div class="table-secondary">{{ row.entity_type }} · {{ row.entity_id||'—' }}</div></template></el-table-column>
        <el-table-column label="操作时间" min-width="160"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="结果" width="110"><template #default><span class="status-pill status-ok">已记录</span></template></el-table-column>
        <el-table-column label="脱敏详情" width="100"><template #default="{row}"><el-button link type="primary" @click="showDetail(row)">查看</el-button></template></el-table-column>
        <template #empty><EmptyState title="暂无审计事件" description="当前权限范围或筛选条件下没有匹配的操作记录。" /></template>
      </el-table>
      <el-table v-else v-loading="loading" :data="loginShown" class="data-table" row-key="id">
        <el-table-column label="账户标识" prop="identifier" min-width="220"/><el-table-column label="结果" width="120"><template #default="{row}"><span class="status-pill" :class="row.result==='success'?'status-ok':row.result==='failure'?'status-danger':'status-pending'">{{ outcomeText(row.result) }}</span></template></el-table-column><el-table-column label="浏览器" prop="user_agent" min-width="230"/><el-table-column label="时间" min-width="160"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column><template #empty><EmptyState title="暂无登录记录" description="登录成功、失败和限流结果会由后端真实记录。" /></template>
      </el-table>
      <div class="pagination-row"><span>{{ mode==='audit'?`第 ${pagination.page} / ${Math.max(1,pagination.pages)} 页`:`第 ${loginPage} / ${Math.max(1,Math.ceil(loginFiltered.length/loginPageSize))} 页` }}</span><el-pagination v-if="mode==='audit'" v-model:current-page="page" v-model:page-size="pageSize" :total="pagination.total" :page-sizes="[10,20,50,100]" layout="sizes, prev, pager, next, jumper"/><el-pagination v-else v-model:current-page="loginPage" v-model:page-size="loginPageSize" :total="loginFiltered.length" :page-sizes="[10,20,50]" layout="sizes, prev, pager, next"/></div>
    </section>
    <el-dialog v-model="detailVisible" title="审计事件详情（已脱敏）" width="540px"><template v-if="selected"><div class="detail-grid"><span>动作</span><strong>{{ selected.action }}</strong><span>实体</span><strong>{{ selected.entity_type }} / {{ selected.entity_id||'—' }}</strong><span>操作人</span><strong>{{ selected.actor_email }}</strong><span>目标用户</span><strong>{{ selected.target_email }}</strong><span>时间</span><strong>{{ dateTime(selected.created_at) }}</strong></div><pre class="metadata-preview">{{ JSON.stringify(selected.metadata||{},null,2) }}</pre><p class="detail-note">日志接口不提供平台凭证、Cookie、Token、密码哈希等敏感字段。</p></template></el-dialog>
  </div>
</template>

<style scoped>.audit-tabs{display:flex;gap:4px;margin:0 0 14px;border-bottom:1px solid #e5ecee}.audit-tabs button{display:flex;align-items:center;gap:7px;border:0;border-bottom:2px solid transparent;padding:10px 13px;margin-bottom:-1px;background:transparent;color:#87949b;font-size:10px}.audit-tabs button.active{border-color:#168174;color:#176f66;font-weight:650}.action-filter{width:160px}.metadata-preview{padding:12px;background:#f5f8f8;border-radius:9px;max-height:260px;overflow:auto;font:10px/1.6 ui-monospace,monospace;color:#536773}.detail-note{color:#8b989e;font-size:9px;line-height:1.6}</style>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, Download, Key, Plus, Search, UserFilled } from '@element-plus/icons-vue'
import api from '../services/api'
import { useAuthStore } from '../stores/auth'
import type { Pagination, User } from '../types'
import { dateTime, errorMessage } from '../utils/format'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const auth=useAuthStore()
const loading=ref(true)
const scopeLoading=ref(false)
const error=ref('')
const users=ref<User[]>([])
const pagination=ref<Pagination>({page:1,page_size:20,total:0,pages:0})
const q=ref('')
const status=ref('')
const role=ref('')
const page=ref(1)
const pageSize=ref(20)
const detailVisible=ref(false)
const selected=ref<User|null>(null)
const resetVisible=ref(false)
const resetToken=ref('')
const resetExpiry=ref('')
const scopes=ref<any[]>([])
const admins=ref<User[]>([])
const ordinaryUsers=ref<User[]>([])
const scopeVisible=ref(false)
const scopeForm=reactive({admin_id:'',user_id:''})
const canEditRoles=computed(()=>auth.isSuperadmin)
let queryTimer:ReturnType<typeof setTimeout>|undefined
watch([q,status,role],()=>{page.value=1;clearTimeout(queryTimer);queryTimer=setTimeout(load,220)})
watch([page,pageSize],load)
async function load(){loading.value=true;error.value='';try{const params=new URLSearchParams({page:String(page.value),page_size:String(pageSize.value)});if(q.value.trim())params.set('q',q.value.trim());if(status.value)params.set('status',status.value);if(role.value)params.set('role',role.value);const {data}=await api.get<{users:User[];pagination:Pagination}>(`/admin/users?${params}`);users.value=data.users;pagination.value=data.pagination}catch(err){error.value=errorMessage(err)}finally{loading.value=false}}
function showDetails(row:User){selected.value=row;detailVisible.value=true}
async function setStatus(row:User){const next=row.status==='active'?'disabled':'active';try{await ElMessageBox.confirm(next==='disabled'?`禁用 ${row.email} 后，其会话和共享授权会撤销，任务也会被取消。`:`启用 ${row.email}。此前撤销的账号授权不会自动恢复。`,'确认用户状态变更',{type:next==='disabled'?'warning':'info',confirmButtonText:next==='disabled'?'确认禁用':'确认启用',cancelButtonText:'取消'});await api.patch(`/admin/users/${row.id}/status`,{status:next});ElMessage.success(next==='disabled'?'用户已禁用':'用户已启用');await load()}catch(err:any){if(err!=='cancel'&&err!=='close')ElMessage.error(errorMessage(err))}}
async function changeRole(row:User,value:string){if(value==='')return;const desired=value==='admin'?['user','admin']:['user'];try{await ElMessageBox.confirm(`将 ${row.email} 的角色调整为“${value==='admin'?'管理员':'普通用户'}”？角色变更会撤销该用户已有会话。`,'调整用户角色',{type:'warning',confirmButtonText:'确认调整',cancelButtonText:'取消'});await api.post(`/admin/users/${row.id}/roles`,{roles:desired});ElMessage.success('用户角色已更新');await load()}catch(err:any){if(err!=='cancel'&&err!=='close')ElMessage.error(errorMessage(err))}}
function handleRoleChange(row:User,value:unknown){void changeRole(row,String(value||''))}
async function revokeSessions(row:User){try{await ElMessageBox.confirm(`撤销 ${row.email} 的所有会话？用户需要重新登录，账户状态和授权不变。`,'撤销用户会话',{type:'warning',confirmButtonText:'确认撤销',cancelButtonText:'取消'});const {data}=await api.post(`/admin/users/${row.id}/sessions/revoke`,{});ElMessage.success(`已撤销 ${data.revoked_count} 个会话`);await load()}catch(err:any){if(err!=='cancel'&&err!=='close')ElMessage.error(errorMessage(err))}}
async function issueReset(row:User){try{await ElMessageBox.confirm(`为 ${row.email} 生成一个短时一次性密码重置令牌？旧令牌会失效。`,'生成重置链接',{type:'warning',confirmButtonText:'生成令牌',cancelButtonText:'取消'});const {data}=await api.post(`/admin/users/${row.id}/reset-link`,{});resetToken.value=data.reset_token;resetExpiry.value=data.expires_at;resetVisible.value=true}catch(err:any){if(err!=='cancel'&&err!=='close')ElMessage.error(errorMessage(err))}}
async function copyReset(){try{await navigator.clipboard.writeText(resetToken.value);ElMessage.success('重置令牌已复制')}catch{ElMessage.warning('浏览器阻止复制，请手动选择复制。')}}
async function loadScopes(){if(!auth.isSuperadmin)return;scopeLoading.value=true;try{const [s,a,u]=await Promise.all([api.get<{scopes:any[]}>('/admin/scopes'),api.get<{users:User[]}>('/admin/users?role=admin&page=1&page_size=100'),api.get<{users:User[]}>('/admin/users?role=user&page=1&page_size=100')]);scopes.value=s.data.scopes;admins.value=a.data.users.filter(x=>x.status==='active');ordinaryUsers.value=u.data.users.filter(x=>x.status==='active'&&!x.roles.includes('admin')&&!x.roles.includes('superadmin'))}catch(err){ElMessage.error(errorMessage(err))}finally{scopeLoading.value=false}}
async function openScopes(){await loadScopes();scopeVisible.value=true}
async function grantScope(){if(!scopeForm.admin_id||!scopeForm.user_id){ElMessage.warning('请选择管理员与普通用户');return}try{await api.post('/admin/scopes',{admin_id:scopeForm.admin_id,user_id:scopeForm.user_id});ElMessage.success('管理范围已授权');scopeForm.user_id='';await loadScopes()}catch(err){ElMessage.error(errorMessage(err))}}
async function revokeScope(row:any){try{await ElMessageBox.confirm(`撤销 ${row.admin_email} 对 ${row.user_email} 的管理范围？`,'撤销管理范围',{type:'warning'});await api.delete(`/admin/scopes/${row.admin_id}/${row.user_id}`);ElMessage.success('管理范围已撤销');await loadScopes()}catch(err:any){if(err!=='cancel'&&err!=='close')ElMessage.error(errorMessage(err))}}
onMounted(async()=>{await load();if(auth.isSuperadmin)await loadScopes()})
</script>

<template>
  <div class="page-wrap">
    <PageHeader eyebrow="IDENTITY & ACCESS" title="用户管理" description="普通管理员只会看到自己管理范围内的用户。禁用、角色调整和重置令牌均调用受服务端授权的真实接口。"><el-button v-if="auth.isSuperadmin" plain @click="openScopes"><el-icon><UserFilled /></el-icon>管理范围</el-button></PageHeader>
    <section class="surface surface-pad">
      <div class="table-toolbar"><div class="toolbar-filters"><el-input v-model="q" class="table-search" clearable placeholder="搜索姓名或邮箱"><template #prefix><el-icon><Search /></el-icon></template></el-input><el-select v-model="status" class="filter-select" clearable placeholder="全部状态"><el-option label="正常" value="active"/><el-option label="已禁用" value="disabled"/></el-select><el-select v-model="role" class="filter-select" clearable placeholder="全部角色"><el-option label="普通用户" value="user"/><el-option label="管理员" value="admin"/><el-option v-if="auth.isSuperadmin" label="超级管理员" value="superadmin"/></el-select></div><span class="muted text-small">{{ pagination.total }} 个可见用户</span></div>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
      <el-table v-loading="loading" :data="users" class="data-table" row-key="id">
        <el-table-column label="用户" min-width="230"><template #default="{row}"><button class="table-user-button" @click="showDetails(row)"><el-avatar :size="31" class="table-avatar">{{ (row.display_name||row.email).slice(0,1).toUpperCase() }}</el-avatar><span><strong>{{ row.display_name||'未设置名称' }}</strong><small>{{ row.email }}</small></span></button></template></el-table-column>
        <el-table-column label="角色" min-width="150"><template #default="{row}"><el-tag v-for="r in row.roles" :key="r" size="small" effect="plain" class="role-tag">{{ r==='superadmin'?'超级管理员':r==='admin'?'管理员':'普通用户' }}</el-tag><el-select v-if="canEditRoles&&!row.roles.includes('superadmin')&&row.id!==auth.user?.id" class="role-select" :model-value="row.roles.includes('admin')?'admin':'user'" size="small" @change="handleRoleChange(row, $event)"><el-option label="普通用户" value="user"/><el-option label="管理员" value="admin"/></el-select></template></el-table-column>
        <el-table-column label="状态" width="115"><template #default="{row}"><span class="status-pill" :class="row.status==='active'?'status-ok':'status-off'">{{ row.status==='active'?'正常':'已禁用' }}</span></template></el-table-column>
        <el-table-column label="注册时间" min-width="150"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="最后登录" min-width="150"><template #default="{row}">{{ dateTime(row.last_login_at) }}</template></el-table-column>
        <el-table-column label="操作" fixed="right" min-width="190"><template #default="{row}"><div class="table-actions"><el-button link type="primary" @click="showDetails(row)">详情</el-button><el-button v-if="row.id!==auth.user?.id&&!row.roles.includes('superadmin')" link type="primary" :disabled="row.status!=='active'" @click="issueReset(row)"><el-icon><Key /></el-icon>重置</el-button><el-button v-if="row.id!==auth.user?.id&&!row.roles.includes('superadmin')" link type="danger" :disabled="row.status!=='active'" @click="revokeSessions(row)">撤销会话</el-button><el-button v-if="row.id!==auth.user?.id&&!row.roles.includes('superadmin')" link :type="row.status==='active'?'danger':'success'" @click="setStatus(row)">{{ row.status==='active'?'禁用':'启用' }}</el-button></div></template></el-table-column>
        <template #empty><EmptyState title="没有找到用户" description="试试更改搜索词或筛选条件。列表只会显示你有权限查看的用户。" /></template>
      </el-table>
      <div class="pagination-row"><span>共 {{ pagination.total }} 个结果</span><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="pagination.total" :page-sizes="[10,20,50,100]" layout="sizes, prev, pager, next, jumper" /></div>
    </section>

    <el-dialog v-model="detailVisible" title="用户详情" width="480px"><template v-if="selected"><div class="detail-grid"><span>显示名称</span><strong>{{ selected.display_name||'未设置' }}</strong><span>邮箱</span><strong>{{ selected.email }}</strong><span>角色</span><strong>{{ selected.roles.map(r=>r==='superadmin'?'超级管理员':r==='admin'?'管理员':'普通用户').join('、') }}</strong><span>账户状态</span><strong>{{ selected.status==='active'?'正常':'已禁用' }}</strong><span>创建时间</span><strong>{{ dateTime(selected.created_at) }}</strong><span>最后登录</span><strong>{{ dateTime(selected.last_login_at) }}</strong><span>内部 ID</span><code>{{ selected.id }}</code></div><p class="detail-note">密码哈希、登录 IP 指纹和账号凭证不会通过用户列表接口返回。</p></template></el-dialog>
    <el-dialog v-model="resetVisible" title="一次性密码重置令牌" width="520px" @closed="resetToken='';resetExpiry=''"><div class="secret-note">令牌只在此次显示一次，有效期至 {{ dateTime(resetExpiry) }}。请通过安全渠道交给用户，不要写入工单或公开聊天。</div><div class="secret-code">{{ resetToken }}</div><template #footer><el-button :icon="CopyDocument" type="primary" @click="copyReset">复制令牌</el-button><el-button @click="resetVisible=false">已妥善保存</el-button></template></el-dialog>
    <el-drawer v-model="scopeVisible" title="管理员管理范围" size="min(680px, 96vw)"><div v-loading="scopeLoading"><div class="notice-strip">普通管理员只能管理明确分配的普通用户。禁用管理员或移除管理员角色时，既有 scope 会被清空。</div><div class="scope-form"><el-select v-model="scopeForm.admin_id" filterable placeholder="选择普通管理员"><el-option v-for="item in admins" :key="item.id" :label="`${item.display_name||item.email} · ${item.email}`" :value="item.id"/></el-select><el-select v-model="scopeForm.user_id" filterable placeholder="选择普通用户"><el-option v-for="item in ordinaryUsers" :key="item.id" :label="`${item.display_name||item.email} · ${item.email}`" :value="item.id"/></el-select><el-button type="primary" :icon="Plus" @click="grantScope">授予范围</el-button></div><el-table :data="scopes" class="data-table scope-table"><el-table-column label="管理员" prop="admin_email" min-width="180"/><el-table-column label="管理用户" prop="user_email" min-width="180"/><el-table-column label="操作" width="90"><template #default="{row}"><el-button link type="danger" @click="revokeScope(row)">撤销</el-button></template></el-table-column><template #empty><EmptyState title="尚未配置范围" description="为管理员分配用户后，其管理能力才会生效。"/></template></el-table></div></el-drawer>
  </div>
</template>

<style scoped>.table-user-button{display:flex;align-items:center;gap:9px;text-align:left;border:0;background:transparent;padding:0}.table-user-button strong,.table-user-button small{display:block}.table-user-button strong{font-size:10px;color:#40545f}.table-user-button small{margin-top:3px;color:#8f9ca3;font-size:9px}.table-avatar{background:#e6f3ee!important;color:#277a6c!important;font-size:10px!important}.role-tag{margin-right:4px}.role-select{width:106px;margin-top:5px}.detail-grid{display:grid;grid-template-columns:110px 1fr;gap:12px 13px;align-items:center;font-size:10px}.detail-grid span{color:#87959d}.detail-grid strong,.detail-grid code{color:#405662;font-weight:550;word-break:break-all}.detail-grid code{font-family:ui-monospace,monospace}.detail-note{padding:10px 12px;margin:18px 0 0;background:#f4f7f8;color:#85929a;border-radius:8px;font-size:9px;line-height:1.6}.scope-form{display:grid;grid-template-columns:1fr 1fr auto;gap:8px;margin:16px 0}.scope-table{margin-top:16px}@media(max-width:700px){.scope-form{grid-template-columns:1fr}.scope-form .el-button{width:100%}}</style>

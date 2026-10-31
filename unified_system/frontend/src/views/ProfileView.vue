<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Lock, Refresh, User } from '@element-plus/icons-vue'
import api from '../services/api'
import { useAuthStore } from '../stores/auth'
import type { SessionInfo } from '../types'
import { dateTime, errorMessage } from '../utils/format'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const auth = useAuthStore()
const loading = ref(true)
const savingProfile = ref(false)
const savingPassword = ref(false)
const sessionsLoading = ref(false)
const error = ref('')
const sessions = ref<SessionInfo[]>([])
const profile = reactive({ display_name: '' })
const passwordForm = reactive({ current_password: '', new_password: '', confirm_password: '' })
onMounted(async()=>{profile.display_name=auth.user?.display_name||'';await loadSessions()})
async function loadSessions(){sessionsLoading.value=true;error.value='';try{const {data}=await api.get<{sessions:SessionInfo[]}>('/auth/sessions');sessions.value=data.sessions}catch(err){error.value=errorMessage(err)}finally{sessionsLoading.value=false;loading.value=false}}
async function saveProfile(){if(!profile.display_name.trim()){ElMessage.warning('请输入显示名称');return}if(profile.display_name.trim().length>120){ElMessage.warning('名称不能超过 120 个字符');return}savingProfile.value=true;try{const {data}=await api.patch('/auth/profile',{display_name:profile.display_name.trim()});if(auth.user)auth.user.display_name=data.user.display_name;ElMessage.success('个人资料已更新')}catch(err){ElMessage.error(errorMessage(err))}finally{savingProfile.value=false}}
async function changePassword(){if(passwordForm.new_password.length<12){ElMessage.warning('新密码至少 12 位');return}if(passwordForm.new_password!==passwordForm.confirm_password){ElMessage.warning('两次输入的新密码不一致');return}savingPassword.value=true;try{await api.post('/auth/password',{current_password:passwordForm.current_password,new_password:passwordForm.new_password});ElMessage.success('密码已更新，请重新登录');auth.clearAuth();window.location.assign('/login')}catch(err){ElMessage.error(errorMessage(err))}finally{savingPassword.value=false}}
async function revokeSession(row:SessionInfo){try{await ElMessageBox.confirm(`确定撤销此登录会话吗？${row.current?'当前设备也会立即退出。':''}`,'撤销会话',{type:'warning',confirmButtonText:'撤销会话',cancelButtonText:'取消'});const {data}=await api.post(`/auth/sessions/${row.id}/revoke`,{});if(data.current_session_revoked){auth.clearAuth();window.location.assign('/login');return}ElMessage.success('会话已撤销');await loadSessions()}catch(err:any){if(err!=='cancel'&&err!=='close')ElMessage.error(errorMessage(err))}}
async function revokeOthers(){const others=sessions.value.filter(row=>!row.current).length;if(!others){ElMessage.info('没有其他有效登录会话');return}try{await ElMessageBox.confirm(`将撤销其他 ${others} 个有效会话，当前设备保持登录。`,'退出其他设备',{type:'warning',confirmButtonText:'确认撤销',cancelButtonText:'取消'});const {data}=await api.post('/auth/sessions/revoke-others',{});ElMessage.success(`已撤销 ${data.revoked_count} 个会话`);await loadSessions()}catch(err:any){if(err!=='cancel'&&err!=='close')ElMessage.error(errorMessage(err))}}
</script>

<template>
  <div class="page-wrap">
    <PageHeader eyebrow="ACCOUNT & SECURITY" title="个人中心" description="查看个人资料、修改密码并管理当前账户的浏览器登录会话。会话列表由服务端维护。" />
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="profile-error" />
    <div class="content-grid equal">
      <section class="surface surface-pad">
        <div class="surface-title"><div><h2><el-icon><User /></el-icon> 基本资料</h2><p>资料由真实账户接口读取并保存。</p></div></div>
        <div class="identity-card"><el-avatar :size="54" class="profile-avatar">{{ (auth.user?.display_name||auth.user?.email||'学').slice(0,1).toUpperCase() }}</el-avatar><div><strong>{{ auth.user?.email }}</strong><span>{{ auth.roles.includes('superadmin')?'超级管理员':auth.roles.includes('admin')?'管理员':'普通用户' }}</span></div></div>
        <el-form label-position="top" class="profile-form"><el-form-item label="显示名称"><el-input v-model="profile.display_name" maxlength="120" show-word-limit /></el-form-item><el-form-item label="注册邮箱"><el-input :model-value="auth.user?.email" disabled /></el-form-item><el-form-item label="账户状态"><div class="status-pill status-ok">{{ auth.user?.status==='active'?'正常使用':'已禁用' }}</div></el-form-item><el-button type="primary" :loading="savingProfile" @click="saveProfile">保存资料</el-button></el-form>
      </section>
      <section class="surface surface-pad">
        <div class="surface-title"><div><h2><el-icon><Lock /></el-icon> 密码与安全</h2><p>更改密码会撤销该账户的现有会话，并要求重新登录。</p></div></div>
        <el-form label-position="top" class="profile-form"><el-form-item label="当前密码"><el-input v-model="passwordForm.current_password" type="password" show-password autocomplete="current-password" /></el-form-item><el-form-item label="新密码"><el-input v-model="passwordForm.new_password" type="password" show-password autocomplete="new-password" placeholder="至少 12 位" /></el-form-item><el-form-item label="确认新密码"><el-input v-model="passwordForm.confirm_password" type="password" show-password autocomplete="new-password" @keyup.enter="changePassword" /></el-form-item><el-button type="primary" plain :loading="savingPassword" @click="changePassword">更新密码</el-button></el-form>
        <div class="security-foot"><span class="status-pill status-ok">HttpOnly 会话</span><span>会话凭证不存储在 LocalStorage</span></div>
      </section>
    </div>
    <section class="surface surface-pad session-card">
      <div class="surface-title"><div><h2>登录会话</h2><p>显示当前有效且由服务端登记的会话；浏览器和设备信息仅用于识别，不包含会话密钥。</p></div><el-button type="danger" plain :disabled="!sessions.some(row=>!row.current)" @click="revokeOthers">退出其他设备</el-button></div>
      <el-table v-loading="sessionsLoading||loading" :data="sessions" class="data-table" row-key="id">
        <el-table-column label="设备 / 浏览器" min-width="240"><template #default="{row}"><div class="table-primary">{{ row.current?'当前设备':'已登录设备' }}</div><div class="table-secondary">{{ row.user_agent || '未提供浏览器信息' }}</div></template></el-table-column>
        <el-table-column label="登录时间" min-width="150"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="最近活动" min-width="150"><template #default="{row}">{{ dateTime(row.last_seen_at) }}</template></el-table-column>
        <el-table-column label="有效期至" min-width="150"><template #default="{row}">{{ dateTime(row.expires_at) }}</template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="{row}"><el-tag v-if="row.current" type="success" effect="light">当前会话</el-tag><el-tag v-else effect="plain">有效</el-tag></template></el-table-column>
        <el-table-column label="操作" width="110"><template #default="{row}"><el-button link type="danger" @click="revokeSession(row)">{{ row.current?'退出此设备':'撤销' }}</el-button></template></el-table-column>
        <template #empty><EmptyState title="没有其他登录会话" description="当前设备会在访问个人中心时登记。" /></template>
      </el-table>
      <div class="session-hint"><el-icon><Refresh /></el-icon>会话有效期按后端策略执行。密码修改、用户禁用和角色变更会撤销已有会话。</div>
    </section>
  </div>
</template>

<style scoped>.profile-error{margin-bottom:14px}.identity-card{display:flex;align-items:center;gap:13px;padding:14px;border:1px solid #edf1f2;border-radius:12px;margin-bottom:17px}.profile-avatar{background:#dff1e9!important;color:#18776b!important;font-weight:700}.identity-card strong,.identity-card span{display:block}.identity-card strong{font-size:11px;color:#384f5c}.identity-card span{margin-top:5px;color:#8e9aa1;font-size:9px}.profile-form{max-width:440px}.security-foot{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-top:19px;padding-top:15px;border-top:1px solid #edf1f2;color:#89969d;font-size:9px}.session-card{margin-top:15px}.session-hint{display:flex;align-items:center;gap:7px;margin-top:13px;color:#8b989f;font-size:9px}</style>

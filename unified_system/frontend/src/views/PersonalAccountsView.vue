<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Edit, Key, Plus, Refresh, RefreshRight } from '@element-plus/icons-vue'
import api from '../services/api'
import type { Account, Platform } from '../types'
import { dateTime, errorMessage, statusClass, statusText } from '../utils/format'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const route = useRoute()
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const rows = ref<Account[]>([])
const platforms = ref<Platform[]>([])
const search = ref('')
const platformFilter = ref(String(route.query.platform || ''))
const page = ref(1)
const pageSize = ref(8)
const editVisible = ref(false)
const credentialsVisible = ref(false)
const credentialMode = ref<'create'|'reauthorize'>('create')
const selected = ref<Account|null>(null)
const editForm = reactive({ label: '' })
const credentialForm = reactive({ platform_key: '', label: '', account_hint: '', auth_type: '', username: '', password: '', cookie_json: '' })
const displayed = computed(() => rows.value.filter(row => (!platformFilter.value || row.platform_key === platformFilter.value) && `${row.platform_name} ${row.label} ${row.account_hint}`.toLowerCase().includes(search.value.trim().toLowerCase())))
const pageRows = computed(() => displayed.value.slice((page.value-1)*pageSize.value, page.value*pageSize.value))
const selectedPlatform = computed(() => platforms.value.find(p => p.key === credentialForm.platform_key))
const selectableMethods = computed(() => (selectedPlatform.value?.auth_methods || []).filter(m => ['username_password','cookies'].includes(m)))
const credentialSupported = computed(() => !!credentialForm.auth_type && ['username_password','cookies'].includes(credentialForm.auth_type))
function resetCredentialForm(account?: Account) {
  const defaultPlatform = account?.platform_key || (platformFilter.value && platforms.value.some(p => p.key === platformFilter.value) ? platformFilter.value : platforms.value[0]?.key || '')
  const methods = platforms.value.find(p => p.key === defaultPlatform)?.auth_methods || []
  Object.assign(credentialForm, { platform_key: defaultPlatform, label: account?.label || '', account_hint: '', auth_type: methods.find(m => ['username_password','cookies'].includes(m)) || '', username: '', password: '', cookie_json: '' })
}
function changePlatform(key: string) {
  const methods = platforms.value.find(p => p.key === key)?.auth_methods || []
  credentialForm.auth_type = methods.find(m => ['username_password','cookies'].includes(m)) || ''
  credentialForm.username = ''; credentialForm.password = ''; credentialForm.cookie_json = ''
}
async function load() {
  loading.value = true; error.value = ''
  try {
    const [accountsResult, platformsResult] = await Promise.all([api.get<{personal_accounts:Account[];shared_accounts:Account[]}>('/platform-accounts'), api.get<{platforms:Platform[]}>('/platforms')])
    rows.value = accountsResult.data.personal_accounts
    platforms.value = platformsResult.data.platforms
    if (platformFilter.value && !platforms.value.some(p=>p.key===platformFilter.value)) platformFilter.value = ''
  } catch (err) { error.value = errorMessage(err) }
  finally { loading.value = false }
}
function openCreate() { credentialMode.value='create'; selected.value=null; resetCredentialForm(); credentialsVisible.value=true }
function openReauthorize(row:Account) { credentialMode.value='reauthorize'; selected.value=row; resetCredentialForm(row); credentialsVisible.value=true }
function credentialPayload(): Record<string,unknown> | null {
  if (credentialForm.auth_type === 'username_password') {
    if (!credentialForm.username.trim() || !credentialForm.password) return null
    return { username: credentialForm.username.trim(), password: credentialForm.password }
  }
  if (credentialForm.auth_type === 'cookies') {
    try {
      const parsed = JSON.parse(credentialForm.cookie_json)
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null
    } catch { return null }
  }
  return null
}
async function submitCredentials() {
  const credentials = credentialPayload()
  if (!credentials) { ElMessage.warning(credentialForm.auth_type==='cookies'?'请填写有效的 JSON Cookie 对象。':'请填写账号和密码。'); return }
  if (credentialMode.value==='create' && !credentialForm.account_hint.trim()) { ElMessage.warning('请填写账号标识，服务端会自动脱敏。'); return }
  saving.value=true
  try {
    if (credentialMode.value==='create') {
      await api.post('/personal-accounts',{platform_key:credentialForm.platform_key,label:credentialForm.label.trim(),account_hint:credentialForm.account_hint.trim(),auth_type:credentialForm.auth_type,credentials})
      ElMessage.success('账号信息已加密保存；该账号仍需真实平台验证。')
    } else if (selected.value) {
      await api.post(`/personal-accounts/${selected.value.id}/reauthorize`,{auth_type:credentialForm.auth_type,credentials})
      ElMessage.success('已提交新凭证，账号状态已重置为未验证。')
    }
    credentialsVisible.value=false
    Object.assign(credentialForm,{username:'',password:'',cookie_json:'',account_hint:''})
    await load()
  } catch(err) { ElMessage.error(errorMessage(err)) }
  finally { saving.value=false }
}
function openEdit(row:Account) { selected.value=row; editForm.label=row.label || ''; editVisible.value=true }
async function saveLabel() {
  if (!selected.value) return
  saving.value=true
  try { await api.patch(`/personal-accounts/${selected.value.id}`,{label:editForm.label.trim()}); ElMessage.success('账号备注已更新'); editVisible.value=false; await load() }
  catch(err) { ElMessage.error(errorMessage(err)) }
  finally { saving.value=false }
}
async function unbind(row:Account) {
  try {
    await ElMessageBox.confirm(`确定解绑「${row.label || row.platform_name}」吗？系统会撤销该账号的加密凭证，并取消相关排队任务。`, '解绑个人账号', { type:'warning', confirmButtonText:'确认解绑', cancelButtonText:'保留账号' })
    await api.delete(`/personal-accounts/${row.id}`); ElMessage.success('账号已解绑'); await load()
  } catch(err:any) { if (err!=='cancel' && err!=='close') ElMessage.error(errorMessage(err)) }
}
onMounted(load)
</script>

<template>
  <div class="page-wrap">
    <PageHeader eyebrow="PERSONAL ACCOUNTS" title="我的个人账号" description="仅显示当前账户拥有的账号。凭证由服务端加密保存，不会在列表或管理员接口中回显。"><el-button type="primary" :icon="Plus" @click="openCreate">添加个人账号</el-button></PageHeader>
    <div class="notice-strip warning account-notice">新增或重新授权只会安全保存凭证。平台尚未接入验证流程，账号会保持“未验证”，不能执行课程任务。</div>
    <section class="surface surface-pad account-surface">
      <div class="table-toolbar"><div class="toolbar-filters"><el-input v-model="search" class="table-search" clearable placeholder="搜索平台、备注或脱敏标识" @input="page=1"><template #prefix><el-icon><Key /></el-icon></template></el-input><el-select v-model="platformFilter" clearable class="filter-select" placeholder="全部平台" @change="page=1"><el-option v-for="p in platforms" :key="p.key" :label="p.name" :value="p.key" /></el-select></div><el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button></div>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
      <el-table v-loading="loading" :data="pageRows" class="data-table" row-key="id">
        <el-table-column label="平台 / 账号" min-width="205"><template #default="{row}"><div class="table-primary">{{ row.platform_name }}</div><div class="table-secondary">{{ row.account_hint }}</div></template></el-table-column>
        <el-table-column label="备注" min-width="135"><template #default="{row}"><span class="table-primary">{{ row.label || '未设置备注' }}</span></template></el-table-column>
        <el-table-column label="授权状态" min-width="120"><template #default="{row}"><span class="status-pill" :class="statusClass(row.status)">{{ statusText(row.status) }}</span><div class="table-secondary">平台未验证账号</div></template></el-table-column>
        <el-table-column label="创建时间" min-width="145"><template #default="{row}">{{ dateTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="平台验证记录" min-width="145"><template #default="{row}">{{ dateTime(row.platform_last_verified_at) }}<div class="table-secondary">不是单账号验证记录</div></template></el-table-column>
        <el-table-column label="操作" fixed="right" min-width="175"><template #default="{row}"><div class="table-actions"><el-button link type="primary" :icon="Edit" @click="openEdit(row)">备注</el-button><el-button link type="primary" :icon="RefreshRight" @click="openReauthorize(row)">重新授权</el-button><el-button link type="danger" :icon="Delete" @click="unbind(row)">解绑</el-button></div></template></el-table-column>
        <template #empty><EmptyState title="还没有个人账号" description="添加后将以加密方式保存凭证；平台接入验证前，账号状态不会变为已验证。" /></template>
      </el-table>
      <div class="pagination-row"><span>共 {{ displayed.length }} 个账号</span><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="displayed.length" :page-sizes="[8,16,32]" layout="sizes, prev, pager, next" /></div>
    </section>

    <el-dialog v-model="credentialsVisible" :title="credentialMode==='create'?'添加个人账号':'重新提交账号凭证'" width="560px" destroy-on-close>
      <div class="secret-note">凭证只发往当前站点后端并加密保存。提交后不会再次显示；请不要粘贴新系统之外的 AI Key 或无关密钥。</div>
      <el-form label-position="top">
        <div v-if="credentialMode==='create'" class="form-grid">
          <el-form-item label="平台" required><el-select v-model="credentialForm.platform_key" style="width:100%" @change="changePlatform"><el-option v-for="p in platforms" :key="p.key" :label="p.name" :value="p.key" /></el-select></el-form-item>
          <el-form-item label="账号备注"><el-input v-model="credentialForm.label" maxlength="120" placeholder="例如：春季学期账号" /></el-form-item>
          <el-form-item label="账号标识" class="full" required><el-input v-model="credentialForm.account_hint" maxlength="120" autocomplete="off" placeholder="邮箱或账号；保存时服务端自动脱敏" /><div class="form-hint">原值不会作为列表字段返回，数据库只保存脱敏标识。</div></el-form-item>
        </div>
        <el-form-item label="授权方式" required><el-select v-model="credentialForm.auth_type" style="width:100%"><el-option v-for="method in selectableMethods" :key="method" :label="method==='username_password'?'账号密码':method==='cookies'?'Cookie 凭证':method" :value="method" /></el-select><div v-if="!selectableMethods.length" class="form-hint">该平台目录没有可通过此表单提交的认证方式；不会模拟二维码或平台登录。</div></el-form-item>
        <template v-if="credentialForm.auth_type==='username_password'"><div class="form-grid"><el-form-item label="平台用户名" required><el-input v-model="credentialForm.username" autocomplete="off" /></el-form-item><el-form-item label="平台密码" required><el-input v-model="credentialForm.password" type="password" show-password autocomplete="new-password" /></el-form-item></div></template>
        <el-form-item v-else-if="credentialForm.auth_type==='cookies'" label="Cookie 凭证 JSON" required><el-input v-model="credentialForm.cookie_json" type="textarea" :rows="5" autocomplete="off" placeholder='{"cookies":"粘贴由你合法取得的 Cookie"}' /><div class="form-hint">前端不保存草稿。请在受信任设备操作；列表不提供凭证回显。</div></el-form-item>
      </el-form>
      <template #footer><el-button @click="credentialsVisible=false">取消</el-button><el-button type="primary" :loading="saving" :disabled="!credentialSupported" @click="submitCredentials">加密提交</el-button></template>
    </el-dialog>

    <el-dialog v-model="editVisible" title="修改账号备注" width="420px"><el-form label-position="top"><el-form-item label="备注"><el-input v-model="editForm.label" maxlength="120" show-word-limit /></el-form-item></el-form><template #footer><el-button @click="editVisible=false">取消</el-button><el-button type="primary" :loading="saving" @click="saveLabel">保存备注</el-button></template></el-dialog>
  </div>
</template>

<style scoped>.account-notice{margin:-3px 0 15px}.account-surface{min-height:250px}.table-search{width:280px}.account-surface :deep(.el-table__empty-text){width:100%}</style>

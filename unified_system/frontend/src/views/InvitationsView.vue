<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, Plus, Search, Ticket } from '@element-plus/icons-vue'
import api from '../services/api'
import { dateTime, errorMessage } from '../utils/format'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

interface InvitationRow {id:string;code_hint:string;created_at:string;created_by:string;created_by_email:string;expires_at:string;max_uses:number;used_count:number;revoked:boolean;note:string}
const loading=ref(true);const saving=ref(false);const error=ref('');const rows=ref<InvitationRow[]>([]);const search=ref('');const filter=ref('');const page=ref(1);const pageSize=ref(10)
const createVisible=ref(false);const usesVisible=ref(false);const codesVisible=ref(false);const usesLoading=ref(false);const selected=ref<InvitationRow|null>(null);const uses=ref<any[]>([]);const oneTimeCodes=ref<{code:string;expires_at:string;max_uses:number}[]>([])
const form=reactive({count:1,max_uses:1,ttl_days:7,note:''})
const filtered=computed(()=>rows.value.filter(row=>{const q=search.value.trim().toLowerCase();const matchText=!q||`${row.code_hint} ${row.created_by_email} ${row.note}`.toLowerCase().includes(q);const now=Date.now();const derived=row.revoked?'revoked':new Date(row.expires_at).getTime()<=now?'expired':row.used_count>=row.max_uses?'exhausted':'usable';return matchText&&(!filter.value||filter.value===derived)}))
const pageRows=computed(()=>filtered.value.slice((page.value-1)*pageSize.value,page.value*pageSize.value))
function inviteState(row:InvitationRow){if(row.revoked)return ['已禁用','status-off'];if(new Date(row.expires_at).getTime()<=Date.now())return ['已过期','status-off'];if(row.used_count>=row.max_uses)return ['次数已用尽','status-pending'];return ['可使用','status-ok']}
async function load(){loading.value=true;error.value='';try{const {data}=await api.get<{invitations:InvitationRow[]}>('/admin/invitations');rows.value=data.invitations}catch(err){error.value=errorMessage(err)}finally{loading.value=false}}
function openCreate(){Object.assign(form,{count:1,max_uses:1,ttl_days:7,note:''});createVisible.value=true}
async function create(){if(!Number.isInteger(form.count)||form.count<1||form.count>100||!Number.isInteger(form.max_uses)||form.max_uses<1||form.max_uses>1000||!Number.isInteger(form.ttl_days)||form.ttl_days<1||form.ttl_days>365){ElMessage.warning('请检查批量数量、单码次数和有效天数范围。');return}saving.value=true;try{const {data}=await api.post('/admin/invitations',{...form});oneTimeCodes.value=data.invitations;createVisible.value=false;codesVisible.value=true;ElMessage.success(`已生成 ${data.invitations.length} 个邀请码；原码只显示这一次。`);await load()}catch(err){ElMessage.error(errorMessage(err))}finally{saving.value=false}}
async function disable(row:InvitationRow){try{await ElMessageBox.confirm(`禁用邀请码 ${row.code_hint}… 后，未使用次数将立即失效。`,'禁用邀请码',{type:'warning',confirmButtonText:'确认禁用',cancelButtonText:'取消'});await api.post(`/admin/invitations/${row.id}/disable`,{});ElMessage.success('邀请码已禁用');await load()}catch(err:any){if(err!=='cancel'&&err!=='close')ElMessage.error(errorMessage(err))}}
async function showUses(row:InvitationRow){selected.value=row;usesVisible.value=true;usesLoading.value=true;try{const {data}=await api.get<{uses:any[]}>(`/admin/invitations/${row.id}/uses`);uses.value=data.uses}catch(err){ElMessage.error(errorMessage(err))}finally{usesLoading.value=false}}
async function copyCode(code:string){try{await navigator.clipboard.writeText(code);ElMessage.success('已复制邀请码')}catch{ElMessage.warning('浏览器阻止了剪贴板访问，请手动复制。')}}
async function copyAll(){const text=oneTimeCodes.value.map(item=>item.code).join('\n');try{await navigator.clipboard.writeText(text);ElMessage.success('邀请码已复制')}catch{ElMessage.warning('浏览器阻止了剪贴板访问，请手动复制。')}}
function closeCodes(){codesVisible.value=false;oneTimeCodes.value=[]}
onMounted(load)
</script>

<template>
  <div class="page-wrap">
    <PageHeader eyebrow="INVITED ACCESS" title="邀请码管理" description="原始邀请码只在创建接口的响应中返回一次。列表只显示后缀提示和使用情况，不能从摘要恢复原码。"><el-button type="primary" :icon="Plus" @click="openCreate">生成邀请码</el-button></PageHeader>
    <div class="notice-strip warning invite-security">请通过可信渠道交付新邀请码。关闭“一次性展示”窗口后，系统不会再次提供原码。</div>
    <section class="surface surface-pad">
      <div class="table-toolbar"><div class="toolbar-filters"><el-input v-model="search" class="table-search" clearable placeholder="搜索后缀、创建者或备注" @input="page=1"><template #prefix><el-icon><Search /></el-icon></template></el-input><el-select v-model="filter" class="filter-select" clearable placeholder="全部状态" @change="page=1"><el-option label="可使用" value="usable"/><el-option label="次数用尽" value="exhausted"/><el-option label="已过期" value="expired"/><el-option label="已禁用" value="revoked"/></el-select></div><span class="muted text-small">{{ rows.length }} 个邀请码</span></div>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
      <el-table v-loading="loading" :data="pageRows" class="data-table" row-key="id">
        <el-table-column label="邀请码提示" min-width="125"><template #default="{row}"><span class="invite-code-hint">•••• {{ row.code_hint }}</span></template></el-table-column>
        <el-table-column label="使用情况" min-width="130"><template #default="{row}"><strong>{{ row.used_count }} / {{ row.max_uses }}</strong><el-progress :percentage="Math.min(100,Math.round(row.used_count/row.max_uses*100))" :show-text="false" :stroke-width="4" /></template></el-table-column>
        <el-table-column label="状态" width="120"><template #default="{row}"><span class="status-pill" :class="inviteState(row)[1]">{{ inviteState(row)[0] }}</span></template></el-table-column>
        <el-table-column label="有效期至" min-width="150"><template #default="{row}">{{ dateTime(row.expires_at) }}</template></el-table-column>
        <el-table-column label="创建者" min-width="170"><template #default="{row}"><div class="table-primary">{{ row.created_by_email||'—' }}</div><div class="table-secondary">{{ dateTime(row.created_at) }}</div></template></el-table-column>
        <el-table-column label="备注" min-width="140"><template #default="{row}">{{ row.note||'—' }}</template></el-table-column>
        <el-table-column label="操作" fixed="right" min-width="155"><template #default="{row}"><div class="table-actions"><el-button link type="primary" @click="showUses(row)">使用记录</el-button><el-button v-if="!row.revoked&&row.used_count<row.max_uses&&new Date(row.expires_at).getTime()>Date.now()" link type="danger" @click="disable(row)">禁用</el-button></div></template></el-table-column>
        <template #empty><EmptyState title="还没有邀请码" description="创建邀请码后可设置单码使用次数和有效期。" /></template>
      </el-table>
      <div class="pagination-row"><span>当前显示 {{ pageRows.length }} / {{ filtered.length }}</span><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="filtered.length" :page-sizes="[10,20,50]" layout="sizes, prev, pager, next" /></div>
    </section>

    <el-dialog v-model="createVisible" title="生成邀请码" width="520px"><el-form label-position="top"><div class="form-grid"><el-form-item label="生成数量"><el-input-number v-model="form.count" :min="1" :max="100"/></el-form-item><el-form-item label="每个邀请码可使用次数"><el-input-number v-model="form.max_uses" :min="1" :max="1000"/></el-form-item><el-form-item label="有效天数"><el-input-number v-model="form.ttl_days" :min="1" :max="365"/></el-form-item><el-form-item label="内部备注"><el-input v-model="form.note" maxlength="200" placeholder="可选，仅管理员可见"/></el-form-item></div></el-form><div class="secret-note">生成后服务端只保存哈希。请在下一步复制原码并妥善交付；关闭后无法再次查看。</div><template #footer><el-button @click="createVisible=false">取消</el-button><el-button type="primary" :loading="saving" @click="create">生成并一次性展示</el-button></template></el-dialog>
    <el-dialog v-model="codesVisible" title="新邀请码 · 仅本次显示" width="580px" :close-on-click-modal="false" @closed="closeCodes"><div class="secret-note">这些原码不会在列表页再次出现。请立即复制到安全渠道，避免通过公开聊天或工单传播。</div><div v-for="item in oneTimeCodes" :key="item.code" class="issued-code"><div class="secret-code">{{ item.code }}</div><div class="issued-meta"><span>有效至 {{ dateTime(item.expires_at) }}</span><span>最多使用 {{ item.max_uses }} 次</span><el-button link type="primary" :icon="CopyDocument" @click="copyCode(item.code)">复制</el-button></div></div><template #footer><el-button type="primary" @click="copyAll">复制全部邀请码</el-button><el-button @click="closeCodes">我已保存，关闭</el-button></template></el-dialog>
    <el-dialog v-model="usesVisible" :title="`使用记录 · ${selected?.code_hint||''}`" width="540px"><el-table v-loading="usesLoading" :data="uses" class="data-table"><el-table-column label="注册用户" prop="user_email" min-width="200"/><el-table-column label="使用时间" min-width="170"><template #default="{row}">{{ dateTime(row.used_at) }}</template></el-table-column><el-table-column label="用户 ID" min-width="100"><template #default="{row}">{{ row.user_id.slice(0,8) }}…</template></el-table-column><template #empty><EmptyState title="暂无使用记录" description="邀请码尚未被成功消费。"/></template></el-table></el-dialog>
  </div>
</template>

<style scoped>.invite-security{margin:-3px 0 15px}.invite-code-hint{font-family:ui-monospace,monospace;color:#315c58;font-size:10px}.issued-code{padding:10px 0;border-bottom:1px solid #eef2f2}.issued-code:last-child{border-bottom:0}.issued-meta{display:flex;align-items:center;gap:12px;margin-top:7px;color:#89969e;font-size:9px}.issued-meta .el-button{margin-left:auto}.el-progress{margin-top:5px}</style>

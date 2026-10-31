<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Collection, Refresh } from '@element-plus/icons-vue'
import api from '../services/api'
import type { Account } from '../types'
import { dateTime, errorMessage, statusClass, statusText } from '../utils/format'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const loading = ref(true)
const error = ref('')
const accounts = ref<Account[]>([])
const search = ref('')
const page = ref(1)
const pageSize = ref(8)
const visible = computed(() => accounts.value.filter(a => `${a.platform_name} ${a.label} ${a.account_hint}`.toLowerCase().includes(search.value.trim().toLowerCase())))
const pageRows = computed(() => visible.value.slice((page.value-1)*pageSize.value,page.value*pageSize.value))
async function load() {
  loading.value=true;error.value=''
  try { const {data}=await api.get<{shared_accounts:Account[]}>('/shared-accounts');accounts.value=data.shared_accounts }
  catch(err){error.value=errorMessage(err);ElMessage.error(error.value)}
  finally{loading.value=false}
}
onMounted(load)
</script>

<template>
  <div class="page-wrap">
    <PageHeader eyebrow="SHARED ACCESS" title="管理员共享账号" description="只展示管理员已开放并且当前授权有效的账号。共享账号凭证不会返回给用户。"><el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button></PageHeader>
    <div class="notice-strip">共享账号的可见性由后端授权决定。授权被撤销、到期或账号关闭后，列表会自动不再显示对应账号。</div>
    <section class="surface surface-pad shared-list">
      <div class="table-toolbar"><el-input v-model="search" class="table-search" clearable placeholder="搜索平台、备注或脱敏账号" @input="page=1"><template #prefix><el-icon><Collection /></el-icon></template></el-input><span class="muted text-small">{{ visible.length }} 个当前可见授权</span></div>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
      <div v-loading="loading">
        <div v-if="pageRows.length" class="shared-grid">
          <article v-for="account in pageRows" :key="account.id" class="shared-card">
            <div class="shared-card-head"><div class="shared-avatar">{{ account.platform_name.slice(0,1) }}</div><span class="status-pill" :class="statusClass(account.status)">{{ statusText(account.status) }}</span></div>
            <div class="shared-title"><div><h3>{{ account.label || account.platform_name }}</h3><p>{{ account.platform_name }} · {{ account.account_hint }}</p></div></div>
            <div class="shared-details"><div><span>授权状态</span><strong>{{ account.available ? '可用（平台已验证）' : '已授权，但平台尚未激活' }}</strong></div><div><span>授权有效期</span><strong>{{ account.grant_expires_at ? dateTime(account.grant_expires_at) : '长期有效，直到撤销' }}</strong></div><div><span>账号有效期</span><strong>{{ account.expires_at ? dateTime(account.expires_at) : '未设置到期日' }}</strong></div><div><span>可用时间</span><strong>{{ account.available_from ? dateTime(account.available_from) : '立即' }}</strong></div></div>
            <div class="shared-card-foot"><el-tag size="small" effect="plain">授权范围 {{ account.access_scope?.task_kinds?.length ? account.access_scope.task_kinds.join('、') : '未限制任务类型' }}</el-tag><el-tag v-if="!account.available" type="warning" size="small">暂不可执行任务</el-tag></div>
          </article>
        </div>
        <EmptyState v-else title="暂无已授权共享账号" description="管理员开放账号并向你授予有效权限后，账号会显示在这里。未授权账号不会出现在用户页面。" />
      </div>
      <div v-if="visible.length" class="pagination-row"><span>共 {{ visible.length }} 项</span><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="visible.length" :page-sizes="[8,16,32]" layout="sizes, prev, pager, next" /></div>
    </section>
  </div>
</template>

<style scoped>.shared-list{margin-top:16px}.shared-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}.shared-card{padding:17px;border:1px solid #e9eff0;border-radius:13px;background:#fff}.shared-card-head{display:flex;align-items:center;justify-content:space-between}.shared-avatar{width:36px;height:36px;display:grid;place-items:center;border-radius:11px;background:#edf5f2;color:#217b70;font-size:14px;font-weight:700}.shared-title h3{margin:12px 0 4px;color:#293f4c;font-size:13px}.shared-title p{margin:0;color:#8b989f;font-size:9px}.shared-details{margin-top:14px;display:grid;grid-template-columns:1fr 1fr;gap:12px}.shared-details span,.shared-details strong{display:block}.shared-details span{color:#97a2a7;font-size:9px}.shared-details strong{margin-top:4px;color:#536874;font-size:9px;font-weight:600}.shared-card-foot{display:flex;gap:6px;flex-wrap:wrap;margin-top:15px;padding-top:12px;border-top:1px solid #eff2f3}@media(max-width:760px){.shared-grid{grid-template-columns:1fr}}</style>

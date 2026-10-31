<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, Bell, CircleCheck, Clock, Collection, Connection, Key, User } from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'
import api from '../services/api'
import type { Account, Platform } from '../types'
import { dateTime, errorMessage, platformInitial, statusClass, statusText } from '../utils/format'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(true)
const error = ref('')
const platforms = ref<Platform[]>([])
const personal = ref<Account[]>([])
const shared = ref<Account[]>([])
const activity = ref<any[]>([])
const tasks = ref<{account_id:string;created_at:string;status:string}[]>([])
const totalAccounts = computed(() => personal.value.length + shared.value.length)
const availablePlatforms = computed(() => platforms.value.filter(p => p.integration_status === 'active').length)
const latestAccounts = computed(() => {
  const accounts = new Map([...personal.value, ...shared.value].map(account => [account.id, account]))
  const latestByAccount = new Map<string,string>()
  for (const task of [...tasks.value].sort((a,b) => b.created_at.localeCompare(a.created_at))) {
    if (accounts.has(task.account_id) && !latestByAccount.has(task.account_id)) latestByAccount.set(task.account_id, task.created_at)
  }
  return [...latestByAccount.entries()].map(([id,last_task_at]) => ({account:accounts.get(id)!,last_task_at})).slice(0,4)
})
const actionLabels: Record<string,string> = {
  'personal_account.created':'添加个人账号', 'personal_account.label_updated':'更新账号备注', 'personal_account.reauthorized':'重新提交授权',
  'personal_account.unbound':'解绑个人账号', 'shared_account.grant_created':'收到共享授权', 'shared_account.grant_revoked':'共享授权已撤销',
  'queued':'任务进入队列', 'leased':'Worker 已领取', 'running':'任务开始运行', 'succeeded':'任务完成', 'failed':'任务失败', 'cancelled':'任务已取消',
}
async function load() {
  loading.value = true; error.value = ''
  try {
    const [platformResponse, accountResponse, activityResponse, taskResponse] = await Promise.all([
      api.get<{platforms:Platform[]}>('/platforms'), api.get<{personal_accounts:Account[];shared_accounts:Account[]}>('/platform-accounts'), api.get<{activity:any[]}>('/activity?limit=8'), api.get<{tasks:{account_id:string;created_at:string;status:string}[]}>('/tasks'),
    ])
    platforms.value = platformResponse.data.platforms
    personal.value = accountResponse.data.personal_accounts
    shared.value = accountResponse.data.shared_accounts
    activity.value = activityResponse.data.activity
    tasks.value = taskResponse.data.tasks
  } catch (err) { error.value = errorMessage(err) }
  finally { loading.value = false }
}
onMounted(load)
</script>

<template>
  <div class="page-wrap">
    <PageHeader eyebrow="LEARNING OVERVIEW" :title="`欢迎回来，${auth.user?.display_name || '同学'}`" description="在一个安全、清楚的工作台中查看账号授权与平台接入状态。" />
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false"><template #default><el-button text type="primary" @click="load">重新加载</el-button></template></el-alert>
    <div v-loading="loading" element-loading-text="正在同步真实账户与平台状态…" class="dashboard-body">
      <section class="hero-card">
        <p class="eyebrow">YOUR LEARNING SPACE</p>
        <h2>学习管理，从清楚开始。</h2>
        <p>平台能力、账号状态和授权范围均来自服务端。当前未验证的平台不会执行任何课程操作。</p>
        <div class="hero-meta"><el-icon><CircleCheck /></el-icon><span>{{ availablePlatforms }} / {{ platforms.length }} 个平台已通过系统验证</span><el-button text type="primary" @click="router.push('/app/platforms')">查看平台 <el-icon><ArrowRight /></el-icon></el-button></div>
      </section>
      <div class="metric-grid">
        <article class="surface metric-card"><div class="metric-top">个人平台账号<span class="metric-icon"><el-icon><Key /></el-icon></span></div><div class="metric-value">{{ personal.length }}</div><div class="metric-foot">仅统计当前账户可见的数据</div></article>
        <article class="surface metric-card"><div class="metric-top">有效共享授权<span class="metric-icon"><el-icon><Collection /></el-icon></span></div><div class="metric-value">{{ shared.length }}</div><div class="metric-foot">只包含已开放且授权有效的账号</div></article>
        <article class="surface metric-card"><div class="metric-top">平台目录<span class="metric-icon"><el-icon><Connection /></el-icon></span></div><div class="metric-value">{{ platforms.length }}</div><div class="metric-foot">平台数量来自后端目录</div></article>
        <article class="surface metric-card"><div class="metric-top">已验证平台<span class="metric-icon"><el-icon><CircleCheck /></el-icon></span></div><div class="metric-value">{{ availablePlatforms }}</div><div class="metric-foot">没有使用模拟状态</div></article>
      </div>
      <div class="content-grid">
        <section class="surface surface-pad">
          <div class="surface-title"><div><h2>平台接入状态</h2><p>认证方式、验证时间和状态说明均来自真实平台目录。</p></div><el-button text type="primary" @click="router.push('/app/platforms')">全部平台 <el-icon><ArrowRight /></el-icon></el-button></div>
          <div v-if="platforms.length" class="platform-grid">
            <article v-for="platform in platforms" :key="platform.key" class="surface platform-card">
              <div class="platform-top"><span class="platform-logo" :class="platform.key==='zhihuishu'?'blue':platform.key==='yinghua'?'amber':''">{{ platformInitial(platform.key) }}</span><span class="status-pill" :class="statusClass(platform.integration_status)">{{ statusText(platform.integration_status) }}</span></div>
              <h3>{{ platform.name }}</h3><p>{{ platform.status_note || '后端未提供平台状态说明。' }}</p>
              <div class="platform-methods"><span v-for="method in platform.auth_methods" :key="method" class="soft-tag">{{ method }}</span><span v-if="!platform.auth_methods?.length" class="soft-tag">未提供授权方式</span></div>
              <div class="card-footer"><span>系统目录 · {{ platform.code_available ? '有候选源码，未等同于接入' : '无已验证接入' }}</span><el-button link type="primary" @click="router.push({path:'/app/accounts',query:{platform:platform.key}})">账号列表</el-button></div>
            </article>
          </div>
          <EmptyState v-else title="暂无平台目录" description="平台信息将在后端目录可用后显示。" />
        </section>
        <div class="side-stack">
          <section class="surface surface-pad">
            <div class="surface-title"><div><h2>最近任务账号</h2><p>根据本人真实任务记录关联账号；不把创建时间伪装成使用时间。</p></div></div>
            <div v-if="latestAccounts.length" class="activity-list">
              <div v-for="item in latestAccounts" :key="item.account.id" class="activity-row"><div class="activity-dot"><el-icon><Key /></el-icon></div><div class="activity-copy"><strong>{{ item.account.label || item.account.platform_name }}</strong><span>{{ item.account.platform_name }} · {{ item.account.account_hint }} · 最近任务 {{ dateTime(item.last_task_at) }}</span></div></div>
            </div>
            <EmptyState v-else title="暂无任务关联账号" description="平台尚未完成验证/接入时无法创建可执行任务；这里不会用账号创建时间代替真实使用时间。" />
            <el-button v-if="totalAccounts" class="full-button" plain @click="router.push('/app/accounts')">管理个人账号</el-button>
          </section>
          <section class="surface surface-pad">
            <div class="surface-title"><div><h2>最近操作</h2><p>只显示服务端记录的本人活动。</p></div><el-icon class="muted"><Clock /></el-icon></div>
            <div v-if="activity.length" class="activity-list"><div v-for="item in activity.slice(0,5)" :key="`${item.type}-${item.id}`" class="activity-row"><div class="activity-dot"><el-icon><User /></el-icon></div><div class="activity-copy"><strong>{{ actionLabels[item.action] || item.action }}</strong><span>{{ item.entity_type }} · {{ dateTime(item.created_at) }}</span></div></div></div>
            <EmptyState v-else title="暂无操作记录" description="当你管理账号或进行任务操作后，记录会显示在这里。" />
          </section>
          <section class="surface surface-pad">
            <div class="surface-title"><div><h2>系统公告</h2><p>公告目前尚未接入后端。</p></div><el-icon class="muted"><Bell /></el-icon></div>
            <EmptyState title="暂无系统公告" description="本系统没有公告接口，当前不展示虚构通知。" />
          </section>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>.dashboard-body{min-height:240px}.side-stack{display:grid;align-content:start;gap:14px}.full-button{width:100%;margin-top:11px}.hero-meta .el-button{margin-left:auto}</style>

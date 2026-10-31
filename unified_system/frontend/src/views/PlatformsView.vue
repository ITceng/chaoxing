<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, Connection, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import api from '../services/api'
import type { Account, Platform } from '../types'
import { dateTime, errorMessage, platformInitial, statusClass, statusText } from '../utils/format'
import PageHeader from '../components/PageHeader.vue'
import EmptyState from '../components/EmptyState.vue'

const router = useRouter()
const loading = ref(true)
const error = ref('')
const platforms = ref<Platform[]>([])
const accounts = ref<Account[]>([])
const authNames: Record<string,string> = { username_password: '账号密码', cookies: 'Cookie 凭证', qr_code: '二维码（未开放）' }
const counts = computed(() => Object.fromEntries(platforms.value.map(p => [p.key, accounts.value.filter(a => a.platform_key === p.key).length])))
async function load() {
  loading.value = true; error.value = ''
  try {
    const [p, a] = await Promise.all([api.get<{platforms:Platform[]}>('/platforms'), api.get<{personal_accounts:Account[];shared_accounts:Account[]}>('/platform-accounts')])
    platforms.value = p.data.platforms
    accounts.value = [...a.data.personal_accounts, ...a.data.shared_accounts]
  } catch (err) { error.value = errorMessage(err); ElMessage.error(error.value) }
  finally { loading.value = false }
}
onMounted(load)
</script>

<template>
  <div class="page-wrap">
    <PageHeader eyebrow="PLATFORM DIRECTORY" title="平台中心" description="目录和能力状态由服务端返回。源码存在不代表 Worker 已接入，待验证平台不会开放课程任务。"><el-button :icon="Refresh" :loading="loading" @click="load">刷新状态</el-button></PageHeader>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="page-alert" />
    <div v-loading="loading" class="platform-grid directory-grid">
      <article v-for="(platform,index) in platforms" :key="platform.key" class="surface platform-card directory-card">
        <div class="platform-top"><div class="platform-logo" :class="index===1?'blue':index===2?'amber':''">{{ platformInitial(platform.key) }}</div><span class="status-pill" :class="statusClass(platform.integration_status)">{{ statusText(platform.integration_status) }}</span></div>
        <div class="platform-title-row"><div><h3>{{ platform.name }}</h3><span class="platform-key">{{ platform.key }}</span></div><span class="account-count"><strong>{{ counts[platform.key] || 0 }}</strong><small>可见账号</small></span></div>
        <p class="directory-note">{{ platform.status_note || '后端没有提供状态说明。' }}</p>
        <div class="directory-meta"><span>支持认证方式</span><div class="platform-methods"><el-tag v-for="method in platform.auth_methods" :key="method" effect="plain" round>{{ authNames[method] || method }}</el-tag><span v-if="!platform.auth_methods?.length" class="soft-tag">未提供</span></div></div>
        <div class="directory-meta"><span>平台最后验证</span><strong>{{ dateTime(platform.last_verified_at) }}</strong></div>
        <div class="directory-meta"><span>候选源码</span><strong>{{ platform.code_available ? '存在（未等于系统接入）' : '未确认' }}</strong></div>
        <div v-if="platform.capabilities?.length" class="directory-meta"><span>后端能力声明</span><div class="platform-methods"><el-tag v-for="cap in platform.capabilities" :key="cap" size="small" type="info">{{ cap }}</el-tag></div></div>
        <div class="directory-foot"><span v-if="platform.integration_status !== 'active'" class="not-ready"><el-icon><Connection /></el-icon>平台 Worker 尚未验证</span><span v-else class="ready-note">系统报告已验证</span><el-button type="primary" plain size="small" @click="router.push({path:'/app/accounts',query:{platform:platform.key}})">管理账号 <el-icon><ArrowRight /></el-icon></el-button></div>
      </article>
    </div>
    <EmptyState v-if="!loading && !platforms.length" title="没有可显示的平台" description="平台目录暂时无法从服务端读取。" />
    <div class="notice-strip warning directory-warning"><strong>能力边界：</strong>当前目录中平台状态由后端实时返回。本系统尚未接入真实平台登录、Worker 执行或课程任务；不要将账号凭证提交给其他非官方页面。</div>
  </div>
</template>

<style scoped>.directory-grid{align-items:stretch}.directory-card{min-height:360px}.platform-title-row{display:flex;align-items:center;justify-content:space-between;margin-top:13px}.platform-title-row h3{margin:0 0 3px}.platform-key{font-size:9px;color:#95a1a8}.account-count{text-align:right}.account-count strong,.account-count small{display:block}.account-count strong{font-size:17px;color:#294551}.account-count small{font-size:9px;color:#96a2a8}.directory-note{margin:14px 0 13px!important;min-height:51px}.directory-meta{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;padding:10px 0;border-top:1px solid #eff2f3;color:#89969d;font-size:9px}.directory-meta strong{color:#526772;font-size:9px;text-align:right;font-weight:600}.directory-meta .platform-methods{margin:0;justify-content:flex-end}.directory-foot{margin-top:auto;padding-top:13px;display:flex;align-items:center;justify-content:space-between;gap:8px}.not-ready,.ready-note{display:flex;align-items:center;gap:6px;color:#947239;font-size:9px}.ready-note{color:#39785f}.directory-warning{margin-top:18px}.page-alert{margin-bottom:15px}</style>

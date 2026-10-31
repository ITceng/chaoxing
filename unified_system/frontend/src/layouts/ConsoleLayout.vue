<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Avatar, Bell, Collection, DataAnalysis, Files, Grid, HomeFilled, Key, Lock, Menu as MenuIcon,
  Monitor, Setting, SwitchButton, Ticket, User, UserFilled,
} from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth'
import { ElMessage, ElMessageBox } from 'element-plus'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const mobileOpen = ref(false)
const activePath = computed(() => route.path)
const userNav = [
  { path: '/app/dashboard', label: '工作台', icon: HomeFilled },
  { path: '/app/platforms', label: '平台中心', icon: Grid },
  { path: '/app/accounts', label: '个人账号', icon: Key },
  { path: '/app/shared', label: '共享账号', icon: Collection },
  { path: '/app/profile', label: '个人中心', icon: User },
]
const adminNav = computed(() => [
  { path: '/app/admin/dashboard', label: '管理概览', icon: DataAnalysis, permission: 'users.read' },
  { path: '/app/admin/users', label: '用户管理', icon: UserFilled, permission: 'users.read' },
  { path: '/app/admin/invitations', label: '邀请码', icon: Ticket, permission: 'invitations.manage' },
  { path: '/app/admin/shared-accounts', label: '共享账号管理', icon: Lock, permission: 'shared_accounts.manage' },
  { path: '/app/admin/audit', label: '审计日志', icon: Files, permission: 'logs.read' },
  { path: '/app/admin/workers', label: '平台与 Worker', icon: Monitor, permission: 'platforms.manage' },
].filter(item => auth.can(item.permission)))
const roleLabel = computed(() => auth.roles.includes('superadmin') ? '超级管理员' : auth.roles.includes('admin') ? '管理员' : '普通用户')
const pageTitle = computed(() => String(route.meta.title || '工作台'))

async function logout() {
  try {
    await ElMessageBox.confirm('退出后需要重新登录才能访问管理面板。', '确认退出', { type: 'warning', confirmButtonText: '退出登录', cancelButtonText: '取消' })
    await auth.logout()
    ElMessage.success('已安全退出')
    router.replace({ name: 'login' })
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '退出失败')
  }
}
function go(path: string) {
  mobileOpen.value = false
  router.push(path)
}
</script>

<template>
  <div class="console-shell">
    <div v-if="mobileOpen" class="mobile-scrim" @click="mobileOpen = false"></div>
    <aside class="sidebar" :class="{ 'sidebar-open': mobileOpen }">
      <div class="brand-lockup" @click="go('/app/dashboard')">
        <span class="brand-symbol">学</span>
        <span class="brand-text"><strong>学习空间</strong><small>统一学习管理系统</small></span>
      </div>
      <div class="workspace-pill"><span class="workspace-dot"></span>安全工作区</div>
      <el-scrollbar class="nav-scroll">
        <div class="nav-caption">工作台</div>
        <el-menu :default-active="activePath" router class="side-menu" @select="mobileOpen = false">
          <el-menu-item v-for="item in userNav" :key="item.path" :index="item.path">
            <el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span>
          </el-menu-item>
        </el-menu>
        <template v-if="adminNav.length">
          <div class="nav-caption nav-caption-admin">系统管理</div>
          <el-menu :default-active="activePath" router class="side-menu" @select="mobileOpen = false">
            <el-menu-item v-for="item in adminNav" :key="item.path" :index="item.path">
              <el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span>
            </el-menu-item>
          </el-menu>
        </template>
      </el-scrollbar>
      <div class="sidebar-bottom">
        <div class="sidebar-help"><span class="help-mark">?</span><div><strong>能力状态透明</strong><small>未验证平台不会开放任务</small></div></div>
        <div class="sidebar-user">
          <el-avatar :size="38" class="user-avatar">{{ (auth.user?.display_name || auth.user?.email || '学').slice(0, 1).toUpperCase() }}</el-avatar>
          <div class="user-meta"><strong>{{ auth.user?.display_name || '学习用户' }}</strong><span>{{ roleLabel }}</span></div>
          <el-dropdown trigger="click" placement="top-end">
            <button class="more-button" aria-label="打开用户菜单"><el-icon><Setting /></el-icon></button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="go('/app/profile')"><el-icon><User /></el-icon>个人中心</el-dropdown-item>
                <el-dropdown-item divided @click="logout"><el-icon><SwitchButton /></el-icon>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>
    </aside>

    <main class="main-column">
      <header class="topbar">
        <div class="topbar-left">
          <el-button class="mobile-trigger" text circle aria-label="打开导航" @click="mobileOpen = !mobileOpen"><el-icon><MenuIcon /></el-icon></el-button>
          <div class="breadcrumb"><span>学习空间</span><i>/</i><strong>{{ pageTitle }}</strong></div>
        </div>
        <div class="topbar-right">
          <div class="service-chip"><span class="pulse-dot"></span><span>安全会话</span></div>
          <el-tooltip content="公告功能尚未接入" placement="bottom">
            <button class="icon-action" aria-label="公告暂不可用"><el-icon><Bell /></el-icon><span class="icon-dot"></span></button>
          </el-tooltip>
          <div class="top-divider"></div>
          <div class="top-user"><span>{{ auth.user?.email }}</span><el-avatar :size="32" class="top-avatar">{{ (auth.user?.display_name || auth.user?.email || '学').slice(0, 1).toUpperCase() }}</el-avatar></div>
        </div>
      </header>
      <div class="view-scroll"><router-view /></div>
      <footer class="app-footer"><span>统一学习管理系统</span><span>仅显示已由后端验证的能力</span></footer>
    </main>
  </div>
</template>

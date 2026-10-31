import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    { path: '/', redirect: '/app/dashboard' },
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: { guestOnly: true, title: '登录' } },
    { path: '/register', name: 'register', component: () => import('../views/RegisterView.vue'), meta: { guestOnly: true, title: '注册' } },
    {
      path: '/app', component: () => import('../layouts/ConsoleLayout.vue'), meta: { requiresAuth: true },
      children: [
        { path: '', redirect: '/app/dashboard' },
        { path: 'dashboard', name: 'dashboard', component: () => import('../views/DashboardView.vue'), meta: { title: '工作台' } },
        { path: 'platforms', name: 'platforms', component: () => import('../views/PlatformsView.vue'), meta: { title: '平台中心' } },
        { path: 'accounts', name: 'accounts', component: () => import('../views/PersonalAccountsView.vue'), meta: { title: '个人账号' } },
        { path: 'shared', name: 'shared', component: () => import('../views/SharedAccountsView.vue'), meta: { title: '共享账号' } },
        { path: 'profile', name: 'profile', component: () => import('../views/ProfileView.vue'), meta: { title: '个人中心' } },
        { path: 'admin', redirect: '/app/admin/dashboard' },
        { path: 'admin/dashboard', name: 'admin-dashboard', component: () => import('../views/AdminDashboardView.vue'), meta: { title: '管理概览', permission: 'users.read' } },
        { path: 'admin/users', name: 'admin-users', component: () => import('../views/UsersView.vue'), meta: { title: '用户管理', permission: 'users.read' } },
        { path: 'admin/invitations', name: 'admin-invitations', component: () => import('../views/InvitationsView.vue'), meta: { title: '邀请码', permission: 'invitations.manage' } },
        { path: 'admin/shared-accounts', name: 'admin-shared', component: () => import('../views/SharedAdminView.vue'), meta: { title: '共享账号管理', permission: 'shared_accounts.manage' } },
        { path: 'admin/audit', name: 'admin-audit', component: () => import('../views/AuditLogsView.vue'), meta: { title: '审计日志', permission: 'logs.read' } },
        { path: 'admin/workers', name: 'admin-workers', component: () => import('../views/WorkersView.vue'), meta: { title: '平台与 Worker', permission: 'platforms.manage' } },
      ],
    },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('../views/NotFoundView.vue'), meta: { title: '页面不存在' } },
  ],
})

router.beforeEach(async to => {
  const auth = useAuthStore()
  try {
    if (!auth.initialized) await auth.initialize()
  } catch {
    ElMessage.error('暂时无法连接服务，请检查网络后重试。')
    if (to.meta.requiresAuth) return { name: 'login' }
  }
  if (to.meta.requiresAuth && !auth.user) return { name: 'login', query: { redirect: to.fullPath } }
  if (to.meta.guestOnly && auth.user) return { name: 'dashboard' }
  const permission = to.meta.permission as string | undefined
  if (permission && !auth.can(permission)) {
    ElMessage.warning('当前账号没有访问此页面的权限。')
    return { name: 'dashboard' }
  }
  return true
})

router.afterEach(to => {
  document.title = `${String(to.meta.title || '工作台')} · 学习空间`
})

export default router

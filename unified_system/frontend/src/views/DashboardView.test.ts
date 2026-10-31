import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia } from 'pinia'
import DashboardView from './DashboardView.vue'
import api from '../services/api'

vi.mock('../services/api', () => ({ default: { get: vi.fn() } }))

const platforms = [
  { key: 'chaoxing', name: '学习通 / 超星', code_available: true, integration_status: 'pending', integrated: false, auth_methods: ['username_password'], capabilities: [], last_verified_at: null, status_note: '尚未接入 Worker' },
  { key: 'zhihuishu', name: '智慧树 / 知到', code_available: true, integration_status: 'pending', integrated: false, auth_methods: ['cookies'], capabilities: [], last_verified_at: null, status_note: '尚未验证' },
  { key: 'yinghua', name: '英华', code_available: false, integration_status: 'pending', integrated: false, auth_methods: [], capabilities: [], last_verified_at: null, status_note: '源码未审计' },
]

async function mountDashboard() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<div />' } }] })
  await router.push('/')
  await router.isReady()
  return mount(DashboardView, { global: { plugins: [createPinia(), router], directives: { loading: () => undefined }, stubs: { 'el-button': { template: '<button><slot /></button>' }, 'el-alert': { template: '<div><slot /></div>' }, 'el-icon': { template: '<i><slot /></i>' } } } })
}

afterEach(() => vi.resetAllMocks())

describe('DashboardView live-data states', () => {
  it('renders only API-provided account and platform values and shows explicit announcement empty state', async () => {
    vi.mocked(api.get).mockImplementation(async (path: any) => {
      if (path === '/platforms') return { data: { platforms } } as any
      if (path === '/platform-accounts') return { data: { personal_accounts: [{ id:'p1', platform_key:'chaoxing', platform_name:'学习通 / 超星', label:'春季账号', account_hint:'s***@example.test', status:'unverified', account_mode:'personal', created_at:'2026-01-02T00:00:00Z' }], shared_accounts: [] } } as any
      if (String(path).startsWith('/activity')) return { data: { activity: [] } } as any
      if (path === '/tasks') return { data: { tasks: [{account_id:'p1',created_at:'2026-02-03T00:00:00Z',status:'queued'}] } } as any
      throw new Error(`Unexpected API call: ${String(path)}`)
    })
    const wrapper = await mountDashboard()
    await flushPromises()
    expect(wrapper.text()).toContain('0 / 3 个平台已通过系统验证')
    expect(wrapper.text()).toContain('春季账号')
    expect(wrapper.text()).toContain('暂无系统公告')
    expect(wrapper.text()).toContain('源码未审计')
    expect(wrapper.text()).toContain('待接入')
    wrapper.unmount()
  })

  it('renders real empty states when the account APIs return empty collections', async () => {
    vi.mocked(api.get).mockImplementation(async (path: any) => {
      if (path === '/platforms') return { data: { platforms: [] } } as any
      if (path === '/platform-accounts') return { data: { personal_accounts: [], shared_accounts: [] } } as any
      if (String(path).startsWith('/activity')) return { data: { activity: [] } } as any
      if (path === '/tasks') return { data: { tasks: [] } } as any
      throw new Error(`Unexpected API call: ${String(path)}`)
    })
    const wrapper = await mountDashboard()
    await flushPromises()
    expect(wrapper.text()).toContain('暂无任务关联账号')
    expect(wrapper.text()).toContain('暂无操作记录')
    expect(wrapper.text()).toContain('暂无系统公告')
    wrapper.unmount()
  })
})

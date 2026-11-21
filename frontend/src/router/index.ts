/**
 * 路由：
 * - 登录页（公开）
 * - 用户工作台（新对话、对话、客户库、客户详情、流程详情、个人设置）
 * - 管理后台（仅管理员：总览、数据统计、人员管理、客户与流程、公式库、模型配置、数据脱敏）
 * - 独立整页视图（文件预览、表格比对）
 */
import { createRouter, createWebHistory } from 'vue-router'

import { setUnauthorizedHandler } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/showcase', alias: ['/landing', '/intro'], name: 'showcase', component: () => import('@/views/ShowcaseView.vue'), meta: { open: true } },
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
    {
      path: '/',
      component: () => import('@/components/layout/AppShell.vue'),
      children: [
        { path: '', name: 'home', component: () => import('@/views/HomeView.vue') },
        { path: 'c/:id', name: 'chat', component: () => import('@/views/ChatView.vue'), props: true, meta: { splitPanel: true } },
        { path: 'customers', name: 'customers', component: () => import('@/views/CustomersView.vue') },
        { path: 'customers/:id', name: 'customer', component: () => import('@/views/CustomerDetailView.vue'), props: true },
        { path: 'customers/:id/recipes/:recipeId', name: 'recipe', component: () => import('@/views/RecipeDetailView.vue'), props: true },
      ],
    },
    {
      path: '/admin',
      component: () => import('@/views/admin/AdminShell.vue'),
      meta: { requiresAdmin: true },
      children: [
        { path: '', redirect: { name: 'admin-overview' } },
        { path: 'overview', name: 'admin-overview', component: () => import('@/views/admin/AdminOverviewView.vue') },
        { path: 'stats', name: 'admin-stats', component: () => import('@/views/admin/AdminStatsView.vue') },
        { path: 'users', name: 'admin-users', component: () => import('@/views/admin/AdminUsersView.vue') },
        { path: 'customers', name: 'admin-customers', component: () => import('@/views/admin/AdminCustomersView.vue') },
        { path: 'formulas', name: 'admin-formulas', component: () => import('@/views/admin/AdminFormulasView.vue') },
        { path: 'models', name: 'admin-models', component: () => import('@/views/admin/AdminModelsView.vue') },
        { path: 'privacy', name: 'admin-privacy', component: () => import('@/views/admin/AdminPrivacyView.vue') },
      ],
    },
    // 旧路径兼容跳转至管理后台
    { path: '/formulas', redirect: { name: 'admin-formulas' } },
    { path: '/stats', redirect: { name: 'admin-stats' } },
    // 文件预览与表格比对：新标签页里整页展示，不带侧边栏
    { path: '/preview/:fileId', name: 'preview', component: () => import('@/views/FilePreviewView.vue') },
    { path: '/compare', name: 'compare', component: () => import('@/views/WorkbookCompareView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

let navigatingToOpen = false

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  navigatingToOpen = Boolean(to.meta.open)
  const user = await auth.ensure()
  navigatingToOpen = false
  if (to.meta.open) return true
  if (to.meta.public) return user ? { name: 'home' } : true
  if (!user) return { name: 'login', query: to.fullPath !== '/' ? { next: to.fullPath } : undefined }
  if (to.matched.some((record) => record.meta.requiresAdmin) && user.role !== 'admin') {
    return { name: 'home' }
  }
  return true
})

setUnauthorizedHandler(() => {
  const auth = useAuthStore()
  auth.clear()
  const current = router.currentRoute.value
  if (!navigatingToOpen && !current.meta.open && current.name !== 'login') router.push({ name: 'login' })
})

export default router

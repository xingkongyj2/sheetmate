/**
 * 路由：登录 / 引导 / 主界面（新对话、对话、客户、客户详情、公式库、设置、统计）。
 * 守卫：未登录 → 登录页；登录后还没做引导 → 引导页。
 */
import { createRouter, createWebHistory } from 'vue-router'

import { setUnauthorizedHandler } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
    { path: '/welcome', name: 'onboarding', component: () => import('@/views/OnboardingView.vue') },
    {
      path: '/',
      component: () => import('@/components/layout/AppShell.vue'),
      children: [
        { path: '', name: 'home', component: () => import('@/views/HomeView.vue') },
        { path: 'c/:id', name: 'chat', component: () => import('@/views/ChatView.vue'), props: true },
        { path: 'customers', name: 'customers', component: () => import('@/views/CustomersView.vue') },
        { path: 'customers/:id', name: 'customer', component: () => import('@/views/CustomerDetailView.vue'), props: true },
        { path: 'formulas', name: 'formulas', component: () => import('@/views/FormulaLibraryView.vue') },
        { path: 'settings', name: 'settings', component: () => import('@/views/SettingsView.vue') },
        { path: 'stats', name: 'stats', component: () => import('@/views/StatsView.vue') },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  const user = await auth.ensure()
  if (to.meta.public) return user ? { name: 'home' } : true
  if (!user) return { name: 'login', query: to.fullPath !== '/' ? { next: to.fullPath } : undefined }
  if (!user.onboarded && to.name !== 'onboarding') return { name: 'onboarding' }
  return true
})

setUnauthorizedHandler(() => {
  const auth = useAuthStore()
  auth.clear()
  if (router.currentRoute.value.name !== 'login') router.push({ name: 'login' })
})

export default router

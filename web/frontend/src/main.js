// 前端入口文件：负责创建 Vue 应用、注册状态管理和路由，再挂载到页面上。
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'

// 路由表定义了“访问哪个地址时显示哪个页面组件”。
const routes = [
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', component: () => import('./views/Dashboard.vue') },
  { path: '/strategies', component: () => import('./views/Strategies.vue') },
  { path: '/positions', component: () => import('./views/Positions.vue') },
  { path: '/orders', component: () => import('./views/Orders.vue') },
  { path: '/trades', component: () => import('./views/Trades.vue') },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 创建应用实例，并把 Pinia 和路由系统挂进去。
const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')

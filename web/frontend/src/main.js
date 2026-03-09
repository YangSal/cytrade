import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'

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

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')

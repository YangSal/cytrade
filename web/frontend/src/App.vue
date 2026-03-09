<template>
  <el-container style="height:100vh">
    <el-aside width="200px" style="background:#001529">
      <div style="color:white;padding:20px;font-size:18px;font-weight:bold">
        cytrade
      </div>
      <el-menu :router="true" background-color="#001529" text-color="#fff"
               active-text-color="#1890ff" default-active="/">
        <el-menu-item index="/dashboard">📊 总览</el-menu-item>
        <el-menu-item index="/strategies">🤖 策略</el-menu-item>
        <el-menu-item index="/positions">💼 持仓</el-menu-item>
        <el-menu-item index="/orders">📋 订单</el-menu-item>
        <el-menu-item index="/trades">💹 成交</el-menu-item>
      </el-menu>
    </el-aside>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { onMounted, onUnmounted } from 'vue'
import { useSystemStore } from './stores/system'

const systemStore = useSystemStore()
let ws = null
let heartbeatTimer = null
let reconnectTimer = null
let reconnectAttempts = 0
let isUnmounted = false

// 停止心跳定时器，避免页面卸载后继续向服务器发 ping。
function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

// 启动心跳，定时向后端发送 ping，帮助维持连接并探测断线。
function startHeartbeat() {
  stopHeartbeat()
  heartbeatTimer = setInterval(() => {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send('ping')
    }
  }, 30000)
}

// WebSocket 断开后按指数退避方式重连，避免频繁无意义重试。
function scheduleReconnect() {
  if (isUnmounted || reconnectTimer) return
  const delay = Math.min(3000 * 2 ** reconnectAttempts, 30000)
  reconnectAttempts += 1
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connectWebSocket()
  }, delay)
}

// 建立实时连接，并把后端推送交给 Pinia store 统一处理。
function connectWebSocket() {
  stopHeartbeat()
  if (ws && ws.readyState === WebSocket.OPEN) return

  const wsProtocol = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${wsProtocol}://${location.host}/ws/realtime`)
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data)
    // 所有实时消息都交给 store 统一分发，页面组件只负责展示。
    systemStore.handleWsMessage(msg)
  }
  ws.onopen = () => {
    reconnectAttempts = 0
    ws.send('ping')
    startHeartbeat()
  }
  ws.onerror = () => ws?.close()
  ws.onclose = () => {
    stopHeartbeat()
    if (!isUnmounted) {
      scheduleReconnect()
    }
  }
}

onMounted(() => {
  // 页面首次挂载时，先拉一次基础状态，再建立实时连接。
  systemStore.fetchStatus()
  connectWebSocket()
})

onUnmounted(() => {
  // 页面卸载时彻底清理定时器和连接，避免内存泄漏。
  isUnmounted = true
  stopHeartbeat()
  if (reconnectTimer) clearTimeout(reconnectTimer)
  ws?.close()
})
</script>

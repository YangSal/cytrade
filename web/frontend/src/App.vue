<template>
  <el-container style="height:100vh">
    <el-aside width="200px" style="background:#001529">
      <div style="color:white;padding:20px;font-size:18px;font-weight:bold">
        CyTrade2
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

function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

function startHeartbeat() {
  stopHeartbeat()
  heartbeatTimer = setInterval(() => {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send('ping')
    }
  }, 30000)
}

function scheduleReconnect() {
  if (isUnmounted || reconnectTimer) return
  const delay = Math.min(3000 * 2 ** reconnectAttempts, 30000)
  reconnectAttempts += 1
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connectWebSocket()
  }, delay)
}

function connectWebSocket() {
  stopHeartbeat()
  if (ws && ws.readyState === WebSocket.OPEN) return

  const wsProtocol = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${wsProtocol}://${location.host}/ws/realtime`)
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data)
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
  systemStore.fetchStatus()
  connectWebSocket()
})

onUnmounted(() => {
  isUnmounted = true
  stopHeartbeat()
  if (reconnectTimer) clearTimeout(reconnectTimer)
  ws?.close()
})
</script>

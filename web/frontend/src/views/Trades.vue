<template>
  <div>
    <h2>成交记录</h2>
    <el-table :data="trades" stripe height="600">
      <el-table-column prop="stock_code" label="标的" width="90" />
      <el-table-column label="回转" width="70">
        <template #default="scope">{{ scope.row.is_t0 ? 'T+0' : 'T+1' }}</template>
      </el-table-column>
      <el-table-column prop="direction_text" label="方向" width="70" />
      <el-table-column prop="price" label="成交价" width="90" :formatter="fmt3" />
      <el-table-column prop="quantity" label="成交量" width="90" />
      <el-table-column prop="amount" label="成交金额" width="110" :formatter="fmt2" />
      <el-table-column prop="buy_commission" label="买佣" width="90" :formatter="fmt2" />
      <el-table-column prop="sell_commission" label="卖佣" width="90" :formatter="fmt2" />
      <el-table-column prop="stamp_tax" label="印花税" width="90" :formatter="fmt2" />
      <el-table-column prop="total_fee" label="总费用" width="90" :formatter="fmt2" />
      <el-table-column prop="trade_id" label="成交编号" width="120" />
      <el-table-column prop="xt_order_id" label="订单号" width="100" />
      <el-table-column prop="order_sysid" label="合同编号" width="140" />
      <el-table-column prop="traded_time" label="成交时间" width="150" />
      <el-table-column prop="order_remark" label="备注" />
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import axios from 'axios'
import { useSystemStore } from '../stores/system'

const store = useSystemStore()
const trades = ref([])
let timer = null

async function load() {
  // 先从后端接口取历史/持久化成交，再和 WebSocket 最近推送的数据合并。
  const res = await axios.get('/api/trades')
  const apiTrades = Array.isArray(res.data) ? res.data : []
  const realtime = Array.isArray(store.recentTrades) ? store.recentTrades : []
  const merged = [...realtime, ...apiTrades]
  const seen = new Set()
  // 使用复合键去重，避免同一笔成交同时出现在轮询结果和实时推送里。
  trades.value = merged.filter(item => {
    const key = `${item.trade_id || ''}_${item.xt_order_id || 0}_${item.traded_time || item.time || ''}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

// 成交金额和费用保留两位小数，成交价保留三位小数。
const fmt2 = (_, __, v) => typeof v === 'number' ? v.toFixed(2) : v
const fmt3 = (_, __, v) => typeof v === 'number' ? v.toFixed(3) : v

onMounted(() => {
  // 首次加载后定时轮询，保持页面数据新鲜。
  load()
  timer = setInterval(load, 3000)
})

onUnmounted(() => {
  // 离开页面时清理轮询定时器。
  if (timer) clearInterval(timer)
})
</script>

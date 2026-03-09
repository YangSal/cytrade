import { defineStore } from 'pinia'
import { ref } from 'vue'
import axios from 'axios'

export const useSystemStore = defineStore('system', () => {
  // status：系统级摘要信息，用于首页状态卡片展示。
  const status = ref({ connected: false, trading_time: false,
                        strategy_count: 0, active_orders: 0,
                        cpu_pct: 0, mem_pct: 0 })
  // positionSummary：持仓汇总统计，用于首页和总览页展示。
  const positionSummary = ref({
    positions_count: 0,
    total_market_value: 0,
    total_cost: 0,
    total_unrealized_pnl: 0,
    total_realized_pnl: 0,
    total_commission: 0,
    total_buy_commission: 0,
    total_sell_commission: 0,
    total_stamp_tax: 0,
    total_fees: 0,
    total_pnl: 0,
  })
  // realtimeTicks：按证券代码保存最新实时行情。
  const realtimeTicks = ref({})
  // recentTrades：保存最近一批成交推送，方便实时展示。
  const recentTrades = ref([])

  async function fetchStatus() {
    // 主动轮询系统状态，作为 WebSocket 的补充。
    try {
      const res = await axios.get('/api/system/status')
      status.value = res.data
    } catch (e) { console.error(e) }
  }

  async function fetchPositionSummary() {
    // 拉取持仓汇总，用于首页仪表盘展示。
    try {
      const res = await axios.get('/api/positions/summary')
      positionSummary.value = res.data
    } catch (e) { console.error(e) }
  }

  function handleWsMessage(msg) {
    // tick 推送按证券代码覆盖，永远保留“最新一条”。
    if (msg.type === 'tick') {
      realtimeTicks.value[msg.code] = msg
      return
    }
    if (msg.type === 'trade_update') {
      // 成交推送按时间倒序插入，只保留最近 100 条，防止数组无限增长。
      recentTrades.value.unshift(msg)
      if (recentTrades.value.length > 100) {
        recentTrades.value.pop()
      }
    }
  }

  return {
    status,
    positionSummary,
    realtimeTicks,
    recentTrades,
    fetchStatus,
    fetchPositionSummary,
    handleWsMessage,
  }
})

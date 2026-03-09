import { defineStore } from 'pinia'
import { ref } from 'vue'
import axios from 'axios'

export const useSystemStore = defineStore('system', () => {
  const status = ref({ connected: false, trading_time: false,
                        strategy_count: 0, active_orders: 0,
                        cpu_pct: 0, mem_pct: 0 })
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
  const realtimeTicks = ref({})
  const recentTrades = ref([])

  async function fetchStatus() {
    try {
      const res = await axios.get('/api/system/status')
      status.value = res.data
    } catch (e) { console.error(e) }
  }

  async function fetchPositionSummary() {
    try {
      const res = await axios.get('/api/positions/summary')
      positionSummary.value = res.data
    } catch (e) { console.error(e) }
  }

  function handleWsMessage(msg) {
    if (msg.type === 'tick') {
      realtimeTicks.value[msg.code] = msg
      return
    }
    if (msg.type === 'trade_update') {
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

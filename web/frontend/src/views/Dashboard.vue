<template>
  <div>
    <h2>系统总览</h2>
    <el-row :gutter="16">
      <el-col :span="6">
        <el-card>
          <div class="metric">
            <div class="label">连接状态</div>
            <div :style="{color: status.connected?'green':'red', fontSize:'20px'}">
              {{ status.connected ? '已连接' : '断开' }}
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <div class="metric">
            <div class="label">运行策略</div>
            <div style="font-size:28px;font-weight:bold">{{ status.strategy_count }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <div class="metric">
            <div class="label">活跃订单</div>
            <div style="font-size:28px;font-weight:bold">{{ status.active_orders }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <div class="metric">
            <div class="label">CPU / 内存</div>
            <div>{{ status.cpu_pct?.toFixed(1) }}% / {{ status.mem_pct?.toFixed(1) }}%</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px;">
      <el-col :span="6">
        <el-card>
          <div class="metric">
            <div class="label">持仓市值</div>
            <div class="number">{{ fmt2(positionSummary.total_market_value) }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <div class="metric">
            <div class="label">总盈亏</div>
            <div class="number" :style="pnlStyle(positionSummary.total_pnl)">
              {{ fmt2(positionSummary.total_pnl) }}
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <div class="metric">
            <div class="label">累计总费用</div>
            <div class="number">{{ fmt2(positionSummary.total_fees) }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <div class="metric">
            <div class="label">持仓标的数</div>
            <div class="number">{{ positionSummary.positions_count }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card style="margin-top: 16px;">
      <template #header>
        <span>费用统计</span>
      </template>
      <el-row :gutter="16">
        <el-col :span="6">
          <div class="metric">
            <div class="label">买入佣金</div>
            <div class="sub-number">{{ fmt2(positionSummary.total_buy_commission) }}</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="metric">
            <div class="label">卖出佣金</div>
            <div class="sub-number">{{ fmt2(positionSummary.total_sell_commission) }}</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="metric">
            <div class="label">印花税</div>
            <div class="sub-number">{{ fmt2(positionSummary.total_stamp_tax) }}</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="metric">
            <div class="label">已实现盈亏</div>
            <div class="sub-number" :style="pnlStyle(positionSummary.total_realized_pnl)">
              {{ fmt2(positionSummary.total_realized_pnl) }}
            </div>
          </div>
        </el-col>
      </el-row>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useSystemStore } from '../stores/system'

const store = useSystemStore()
const { status, positionSummary } = storeToRefs(store)
let timer = null

function refresh() {
  store.fetchStatus()
  store.fetchPositionSummary()
}

function fmt2(value) {
  return typeof value === 'number' ? value.toFixed(2) : value
}

function pnlStyle(value) {
  if (typeof value !== 'number') return {}
  if (value > 0) return { color: '#f56c6c' }
  if (value < 0) return { color: '#67c23a' }
  return {}
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 5000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.metric { text-align: center; padding: 10px 0; }
.label { color: #999; margin-bottom: 8px; }
.number { font-size: 24px; font-weight: bold; }
.sub-number { font-size: 20px; font-weight: bold; }
</style>

<template>
  <div>
    <h2>策略管理</h2>
    <el-table :data="strategies" stripe>
      <el-table-column prop="strategy_name" label="策略" />
      <el-table-column prop="stock_code" label="标的" />
      <el-table-column prop="status" label="状态">
        <template #default="{ row }">
          <el-tag :type="tagType(row.status)">{{ row.status_text || statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="total_quantity" label="持仓量" />
      <el-table-column prop="avg_cost" label="均价" :formatter="fmt2" />
      <el-table-column prop="unrealized_pnl" label="浮动盈亏" :formatter="fmt2" />
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button size="small" @click="pause(row)">暂停</el-button>
          <el-button size="small" type="primary" @click="resume(row)">恢复</el-button>
          <el-button size="small" type="danger" @click="close(row)">平仓</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { strategyStatusText, strategyStatusTagType } from '../utils/status'

const strategies = ref([])

async function load() {
  // 拉取当前所有策略的最新状态。
  const res = await axios.get('/api/strategies')
  strategies.value = res.data
}

async function pause(row) {
  // 调用后端接口暂停策略，然后重新刷新列表。
  await axios.post(`/api/strategies/${row.strategy_id}/pause`)
  ElMessage.success('已暂停')
  load()
}

async function resume(row) {
  // 调用后端接口恢复策略，然后重新刷新列表。
  await axios.post(`/api/strategies/${row.strategy_id}/resume`)
  ElMessage.success('已恢复')
  load()
}

async function close(row) {
  // 平仓属于风险较高操作，先弹确认框，避免误点。
  await ElMessageBox.confirm(`确认对 ${row.stock_code} 执行强制平仓？`, '确认', { type: 'warning' })
  await axios.post(`/api/strategies/${row.strategy_id}/close`)
  ElMessage.success('平仓指令已发送')
  load()
}

// 页面内统一的数字格式化函数。
const fmt2 = (_, __, val) => typeof val === 'number' ? val.toFixed(2) : val
const statusText = strategyStatusText
const tagType = strategyStatusTagType

onMounted(() => {
  load(); setInterval(load, 5000)
})
</script>

<template>
  <div>
    <h2>订单记录</h2>
    <el-table :data="orders" stripe height="600">
      <el-table-column prop="stock_code" label="标的" width="80" />
      <el-table-column prop="direction" label="方向" width="70">
        <template #default="{ row }">
          {{ row.direction_text || directionText(row.direction) }}
        </template>
      </el-table-column>
      <el-table-column prop="order_type" label="类型" width="90">
        <template #default="{ row }">
          {{ row.order_type_text || typeText(row.order_type) }}
        </template>
      </el-table-column>
      <el-table-column prop="price" label="委托价" width="80" :formatter="fmt3" />
      <el-table-column prop="quantity" label="委托量" width="80" />
      <el-table-column prop="status" label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="tagType(row.status)" size="small">{{ row.status_text || statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="filled_quantity" label="成交量" width="80" />
      <el-table-column prop="filled_avg_price" label="成交均价" width="90" :formatter="fmt3" />
      <el-table-column prop="remark" label="备注" />
      <el-table-column prop="create_time" label="时间" width="160" />
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'
import {
  orderStatusText,
  orderStatusTagType,
  orderDirectionText,
  orderTypeText,
} from '../utils/status'

const orders = ref([])
async function load() {
  // 订单页采用简单轮询，定期从后端取最新订单状态。
  const res = await axios.get('/api/orders')
  orders.value = res.data
}
// 统一把价格按三位小数展示，更适合股票价格阅读。
const fmt3 = (_, __, v) => typeof v === 'number' ? v.toFixed(3) : v
const directionText = orderDirectionText
const typeText = orderTypeText
const statusText = orderStatusText
const tagType = orderStatusTagType
onMounted(() => {
  // 进入页面先加载一次，再每 3 秒刷新一次。
  load(); setInterval(load, 3000)
})
</script>

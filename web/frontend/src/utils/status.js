// 这个文件集中放前端展示用的状态映射函数。
// 这样页面组件只要关心“传入什么值”，不用每个页面都重复写判断逻辑。

export const orderStatusText = (status) => ({
  UNREPORTED: '未报',
  WAIT_REPORTING: '待报',
  REPORTED: '已报',
  REPORTED_CANCEL: '已报待撤',
  PARTSUCC_CANCEL: '部成待撤',
  PART_CANCEL: '部撤',
  CANCELED: '已撤',
  PART_SUCC: '部成',
  SUCCEEDED: '已成',
  JUNK: '废单',
  UNKNOWN: '未知'
}[status] || status)

// Element Plus 的 Tag 组件需要 type 值，这里统一做映射。
export const orderStatusTagType = (status) => ({
  SUCCEEDED: 'success',
  PART_SUCC: 'warning',
  PARTSUCC_CANCEL: 'warning',
  REPORTED_CANCEL: 'warning',
  CANCELED: 'info',
  PART_CANCEL: 'info',
  JUNK: 'danger',
  UNKNOWN: 'danger',
  UNREPORTED: '',
  WAIT_REPORTING: '',
  REPORTED: ''
}[status] || '')

export const strategyStatusText = (status) => ({
  INITIALIZING: '初始化中',
  RUNNING: '运行中',
  PAUSED: '暂停',
  STOPPED: '已停止',
  ERROR: '异常'
}[status] || status)

export const strategyStatusTagType = (status) => ({
  RUNNING: 'success',
  PAUSED: 'warning',
  STOPPED: 'info',
  ERROR: 'danger',
  INITIALIZING: ''
}[status] || '')

// 买卖方向的展示文本。
export const orderDirectionText = (direction) => ({
  BUY: '买入',
  SELL: '卖出'
}[direction] || direction)

// 委托类型的展示文本。
export const orderTypeText = (orderType) => ({
  LIMIT: '限价',
  MARKET: '市价',
  BY_AMOUNT: '按金额',
  BY_QUANTITY: '按数量'
}[orderType] || orderType)

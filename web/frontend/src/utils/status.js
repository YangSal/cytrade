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

export const orderDirectionText = (direction) => ({
  BUY: '买入',
  SELL: '卖出'
}[direction] || direction)

export const orderTypeText = (orderType) => ({
  LIMIT: '限价',
  MARKET: '市价',
  BY_AMOUNT: '按金额',
  BY_QUANTITY: '按数量'
}[orderType] || orderType)

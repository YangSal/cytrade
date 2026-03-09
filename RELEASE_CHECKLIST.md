# Release Checklist

## 1. 安全检查

- [ ] 仓库中不存在真实账号、密码、Token、Webhook
- [ ] `.env`、数据库文件、日志、状态文件未被提交
- [ ] README 中示例均为占位值

## 2. 代码检查

- [ ] 主入口可正常装配
- [ ] 重连后订阅恢复链路可用
- [ ] 交易日控制链路可用（非交易日不激活策略）
- [ ] Web 撤单走真实执行链路
- [ ] 持仓恢复线程安全路径可用
- [ ] 策略停止后归档联动正常
- [ ] 手续费与印花税计算符合费率表/默认费率配置
- [ ] `T+0/T+1` 可用仓位规则符合预期

## 3. 测试检查

- [ ] 执行 `python -m pytest tests/ -v`
- [ ] 测试基线记录与 README 一致
- [ ] 若改动配置/装配逻辑，确认 `tests/test_main.py` 通过
- [ ] 若改动持久化逻辑，确认 `tests/test_data_manager.py` 通过
- [ ] 若改动费用逻辑，确认 `tests/test_fee_schedule.py`、`tests/test_position.py`、`tests/test_order_manager.py` 通过
- [ ] 若改动前端展示，确认 `web/frontend` 可成功执行 `npm run build`

## 4. 文档检查

- [ ] README 已更新到最新能力与基线
- [ ] CHANGELOG 已记录本次发布的核心功能
- [ ] `终审.md` 结论与当前代码一致
- [ ] `整改追踪表.md` 与审查文档口径一致
- [ ] 若新增接口，README 中已补充说明

## 5. 开源发布检查

- [ ] 选择并补充合适的 License
- [ ] 确认是否需要 Release Notes
- [ ] 确认是否需要隐藏内部专用文档或示例数据
- [ ] 确认 Issue / PR 流程是否准备完成

## 6. PyPI 发布检查

- [ ] 执行 `python -m build`
- [ ] 执行 `python -m twine check dist/*`
- [ ] 配置 `TWINE_USERNAME=__token__`
- [ ] 配置 `TWINE_PASSWORD=<pypi token>`
- [ ] 执行 `python -m twine upload dist/*`

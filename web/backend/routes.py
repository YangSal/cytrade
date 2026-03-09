"""API 路由定义"""
from datetime import datetime
from typing import List, Optional

try:
    from fastapi import APIRouter, HTTPException, Query
    _FASTAPI = True
except ImportError:
    _FASTAPI = False
    APIRouter = object
    HTTPException = Exception
    Query = lambda *a, **kw: None  # type: ignore

from web.backend.schemas import (
    StrategyInfo, PositionDetail, PositionSummary,
    OrderInfo, TradeInfo, SystemStatus, ActionResponse
)
from web.backend.status_map import (
    order_status_text,
    strategy_status_text,
    order_direction_text,
    order_type_text,
)

# 依赖由 main.py 注入
_strategy_runner = None
_position_manager = None
_order_manager = None
_data_manager = None
_connection_manager = None
_trade_executor = None
_ws_manager = None

if _FASTAPI:
    router = APIRouter()
else:
    router = None


if _FASTAPI and router is not None:

    # ------------------------------------------------------------------ 策略

    @router.get("/strategies", response_model=List[StrategyInfo], tags=["策略"])
    async def get_strategies():
        """获取所有策略列表"""
        if not _strategy_runner:
            return []
        result = []
        for s in _strategy_runner.get_all_strategies():
            pos = None
            if _position_manager:
                pos = _position_manager.get_position(s.strategy_id)
            result.append(StrategyInfo(
                strategy_id=s.strategy_id,
                strategy_name=s.strategy_name,
                stock_code=s.stock_code,
                status=s.status.value,
                status_text=strategy_status_text(s.status.value),
                unrealized_pnl=pos.unrealized_pnl if pos else 0.0,
                realized_pnl=pos.realized_pnl if pos else 0.0,
                total_quantity=pos.total_quantity if pos else 0,
                avg_cost=pos.avg_cost if pos else 0.0,
                current_price=pos.current_price if pos else 0.0,
            ))
        return result

    @router.get("/strategies/{strategy_id}", response_model=StrategyInfo, tags=["策略"])
    async def get_strategy(strategy_id: str):
        if not _strategy_runner:
            raise HTTPException(status_code=503, detail="StrategyRunner 未初始化")
        s = _strategy_runner.get_strategy(strategy_id)
        if not s:
            raise HTTPException(status_code=404, detail="策略不存在")
        pos = _position_manager.get_position(strategy_id) if _position_manager else None
        return StrategyInfo(
            strategy_id=s.strategy_id,
            strategy_name=s.strategy_name,
            stock_code=s.stock_code,
            status=s.status.value,
            status_text=strategy_status_text(s.status.value),
            unrealized_pnl=pos.unrealized_pnl if pos else 0.0,
            realized_pnl=pos.realized_pnl if pos else 0.0,
            total_quantity=pos.total_quantity if pos else 0,
            avg_cost=pos.avg_cost if pos else 0.0,
            current_price=pos.current_price if pos else 0.0,
        )

    @router.post("/strategies/{strategy_id}/pause", response_model=ActionResponse, tags=["策略"])
    async def pause_strategy(strategy_id: str):
        if not _strategy_runner:
            raise HTTPException(status_code=503, detail="StrategyRunner 未初始化")
        s = _strategy_runner.get_strategy(strategy_id)
        if not s:
            raise HTTPException(status_code=404, detail="策略不存在")
        s.pause()
        return ActionResponse(success=True, message=f"策略 {strategy_id[:8]} 已暂停")

    @router.post("/strategies/{strategy_id}/resume", response_model=ActionResponse, tags=["策略"])
    async def resume_strategy(strategy_id: str):
        if not _strategy_runner:
            raise HTTPException(status_code=503, detail="StrategyRunner 未初始化")
        s = _strategy_runner.get_strategy(strategy_id)
        if not s:
            raise HTTPException(status_code=404, detail="策略不存在")
        s.resume()
        return ActionResponse(success=True, message=f"策略 {strategy_id[:8]} 已恢复")

    @router.post("/strategies/{strategy_id}/close", response_model=ActionResponse, tags=["策略"])
    async def close_strategy(strategy_id: str):
        """强制平仓"""
        if not _strategy_runner:
            raise HTTPException(status_code=503, detail="StrategyRunner 未初始化")
        s = _strategy_runner.get_strategy(strategy_id)
        if not s:
            raise HTTPException(status_code=404, detail="策略不存在")
        s.close_position(remark="Web 强制平仓")
        return ActionResponse(success=True, message=f"策略 {strategy_id[:8]} 平仓指令已发送")

    # ------------------------------------------------------------------ 持仓

    @router.get("/positions", response_model=List[PositionDetail], tags=["持仓"])
    async def get_positions():
        if not _position_manager:
            return []
        positions = _position_manager.get_all_positions()
        result = []
        for pos in positions.values():
            result.append(PositionDetail(
                strategy_id=pos.strategy_id,
                strategy_name=pos.strategy_name,
                stock_code=pos.stock_code,
                total_quantity=pos.total_quantity,
                available_quantity=pos.available_quantity,
                is_t0=pos.is_t0,
                avg_cost=pos.avg_cost,
                current_price=pos.current_price,
                market_value=pos.market_value,
                unrealized_pnl=pos.unrealized_pnl,
                unrealized_pnl_ratio=pos.unrealized_pnl_ratio,
                realized_pnl=pos.realized_pnl,
                total_commission=pos.total_commission,
                total_buy_commission=pos.total_buy_commission,
                total_sell_commission=pos.total_sell_commission,
                total_stamp_tax=pos.total_stamp_tax,
                total_fees=pos.total_fees,
                update_time=pos.update_time.isoformat(),
            ))
        return result

    @router.get("/positions/summary", response_model=PositionSummary, tags=["持仓"])
    async def get_position_summary():
        if not _position_manager:
            return PositionSummary(**{k: 0 for k in PositionSummary.model_fields})
        summary = _position_manager.get_position_summary()
        return PositionSummary(**summary)

    # ------------------------------------------------------------------ 订单

    @router.get("/orders", response_model=List[OrderInfo], tags=["订单"])
    async def get_orders(strategy_id: Optional[str] = Query(None)):
        if not _order_manager:
            return []
        orders = (_order_manager.get_orders_by_strategy(strategy_id)
                  if strategy_id else list(_order_manager._orders.values()))
        result = []
        for o in orders:
            result.append(OrderInfo(
                order_uuid=o.order_uuid,
                strategy_id=o.strategy_id,
                strategy_name=o.strategy_name,
                stock_code=o.stock_code,
                direction=o.direction.value,
                direction_text=order_direction_text(o.direction.value),
                order_type=o.order_type.value,
                order_type_text=order_type_text(o.order_type.value),
                price=o.price,
                quantity=o.quantity,
                status=o.status.value,
                status_text=order_status_text(o.status.value),
                filled_quantity=o.filled_quantity,
                filled_avg_price=o.filled_avg_price,
                filled_amount=o.filled_amount,
                commission=o.commission,
                remark=o.remark,
                create_time=o.create_time.isoformat(),
                update_time=o.update_time.isoformat(),
            ))
        return result

    @router.get("/orders/{order_uuid}", response_model=OrderInfo, tags=["订单"])
    async def get_order(order_uuid: str):
        if not _order_manager:
            raise HTTPException(status_code=503, detail="OrderManager 未初始化")
        o = _order_manager.get_order(order_uuid)
        if not o:
            raise HTTPException(status_code=404, detail="订单不存在")
        return OrderInfo(
            order_uuid=o.order_uuid,
            strategy_id=o.strategy_id,
            strategy_name=o.strategy_name,
            stock_code=o.stock_code,
            direction=o.direction.value,
            direction_text=order_direction_text(o.direction.value),
            order_type=o.order_type.value,
            order_type_text=order_type_text(o.order_type.value),
            price=o.price,
            quantity=o.quantity,
            status=o.status.value,
            status_text=order_status_text(o.status.value),
            filled_quantity=o.filled_quantity,
            filled_avg_price=o.filled_avg_price,
            filled_amount=o.filled_amount,
            commission=o.commission,
            remark=o.remark,
            create_time=o.create_time.isoformat(),
            update_time=o.update_time.isoformat(),
        )

    @router.post("/orders/{order_uuid}/cancel", response_model=ActionResponse, tags=["订单"])
    async def cancel_order(order_uuid: str):
        if not _order_manager or not _trade_executor:
            raise HTTPException(status_code=503, detail="OrderManager/TradeExecutor 未初始化")
        o = _order_manager.get_order(order_uuid)
        if not o:
            raise HTTPException(status_code=404, detail="订单不存在")
        if not o.is_active():
            return ActionResponse(success=False, message="订单已终结，无法撤单")

        # 通过 TradeExecutor 走真实撤单链路
        try:
            ok = _trade_executor.cancel_order(order_uuid, remark="Web撤单")
            if not ok:
                return ActionResponse(success=False, message=f"撤单请求失败 {order_uuid[:8]}")
            return ActionResponse(success=True, message=f"撤单请求已发送 {order_uuid[:8]}")
        except Exception as e:
            return ActionResponse(success=False, message=f"撤单失败: {str(e)}")

    # ------------------------------------------------------------------ 成交

    @router.get("/trades", response_model=List[TradeInfo], tags=["成交"])
    async def get_trades(strategy_id: Optional[str] = Query(None),
                         start_date: Optional[str] = Query(None),
                         end_date: Optional[str] = Query(None)):
        if not _data_manager:
            return []
        records = _data_manager.query_trades(strategy_id, start_date, end_date)
        result = []
        for t in records:
            direction = str(t.get("direction", "") or "")
            result.append(TradeInfo(
                trade_id=str(t.get("trade_id", "") or ""),
                xt_order_id=int(t.get("xt_order_id", 0) or 0),
                order_uuid=str(t.get("order_uuid", "") or ""),
                strategy_id=str(t.get("strategy_id", "") or ""),
                strategy_name=str(t.get("strategy_name", "") or ""),
                stock_code=str(t.get("stock_code", "") or ""),
                account_type=int(t.get("account_type", 0) or 0),
                account_id=str(t.get("account_id", "") or ""),
                order_type=int(t.get("order_type", 0) or 0),
                traded_time=int(t.get("traded_time", 0) or 0),
                order_sysid=str(t.get("order_sysid", "") or ""),
                order_remark=str(t.get("order_remark", "") or ""),
                xt_direction=int(t.get("xt_direction", 0) or 0),
                offset_flag=int(t.get("offset_flag", 0) or 0),
                direction=direction,
                direction_text=order_direction_text(direction),
                price=float(t.get("price", 0) or 0),
                quantity=int(t.get("quantity", 0) or 0),
                amount=float(t.get("amount", 0) or 0),
                commission=float(t.get("commission", 0) or 0),
                buy_commission=float(t.get("buy_commission", 0) or 0),
                sell_commission=float(t.get("sell_commission", 0) or 0),
                stamp_tax=float(t.get("stamp_tax", 0) or 0),
                total_fee=float(t.get("total_fee", t.get("commission", 0)) or 0),
                is_t0=bool(t.get("is_t0", 0)),
                trade_time=str(t.get("trade_time", "") or ""),
            ))
        return result

    # ------------------------------------------------------------------ 系统

    @router.get("/system/status", response_model=SystemStatus, tags=["系统"])
    async def get_system_status():
        connected = _connection_manager.is_connected() if _connection_manager else False
        trading_time = _strategy_runner.is_trading_time() if _strategy_runner else False
        strategy_count = len(_strategy_runner.get_all_strategies()) if _strategy_runner else 0
        active_orders = len(_order_manager.get_active_orders()) if _order_manager else 0

        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory().percent
        except Exception:
            cpu = mem = 0.0

        return SystemStatus(
            connected=connected,
            trading_time=trading_time,
            strategy_count=strategy_count,
            active_orders=active_orders,
            cpu_pct=cpu,
            mem_pct=mem,
            timestamp=datetime.now().isoformat(),
        )

    @router.get("/system/logs", tags=["系统"])
    async def get_logs(lines: int = Query(100)):
        """读取最近 N 行系统日志"""
        import os
        log_file = "./logs/system.log"
        if not os.path.exists(log_file):
            return {"logs": []}
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
            return {"logs": all_lines[-lines:]}
        except Exception as e:
            return {"logs": [], "error": str(e)}

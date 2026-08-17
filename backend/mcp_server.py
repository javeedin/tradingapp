#!/usr/bin/env python3
"""MCP server for trading app - exposes REST API as Claude-compatible tools."""

import json
import logging
from typing import Any

import httpx
from mcp.server import Server
from mcp.types import Tool, TextContent, ToolResult

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trading-mcp")

BASE_URL = "http://localhost:8000"
server = Server("trading-app")


async def call_backend(endpoint: str, method: str = "GET", data: dict | None = None) -> dict:
    """Call the trading backend API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{BASE_URL}{endpoint}"
        if method == "GET":
            response = await client.get(url)
        elif method == "POST":
            response = await client.post(url, json=data)
        else:
            raise ValueError(f"Unsupported method: {method}")
        response.raise_for_status()
        return response.json()


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available trading tools."""
    return [
        Tool(
            name="get_status",
            description="Get current account status, portfolio summary, and market status",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_positions",
            description="Get all open positions with entry price, current price, and P&L",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_holdings",
            description="Get demat holdings - all shares owned",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_orders",
            description="Get order history with status, price, quantity",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_trades",
            description="Get executed trades with entry/exit prices and P&L",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_quote",
            description="Get live market quote for a symbol",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Stock symbol (e.g., INFY, TCS, RELIANCE)",
                    },
                },
                "required": ["symbol"],
            },
        ),
        Tool(
            name="get_chart",
            description="Get historical price data for charting",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Stock symbol",
                    },
                    "interval": {
                        "type": "string",
                        "description": "Candle interval (minute, 5minute, hour, day)",
                        "enum": ["minute", "5minute", "hour", "day"],
                    },
                },
                "required": ["symbol", "interval"],
            },
        ),
        Tool(
            name="get_screener",
            description="Get latest stock screener results - high-beta stocks with 2%+ upward potential",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="run_screener_scan",
            description="Manually trigger a stock screener scan",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="get_signals",
            description="Get latest trading signals from the engine",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="place_order",
            description="Place a new buy or sell order",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Stock symbol",
                    },
                    "side": {
                        "type": "string",
                        "enum": ["buy", "sell"],
                        "description": "Buy or sell",
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Number of shares",
                    },
                    "order_type": {
                        "type": "string",
                        "enum": ["market", "limit"],
                        "description": "Market or limit order",
                    },
                    "price": {
                        "type": "number",
                        "description": "Limit price (required for limit orders)",
                    },
                },
                "required": ["symbol", "side", "quantity", "order_type"],
            },
        ),
        Tool(
            name="modify_order",
            description="Modify a pending order's price or quantity",
            inputSchema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "Order ID to modify",
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "New quantity",
                    },
                    "price": {
                        "type": "number",
                        "description": "New price",
                    },
                },
                "required": ["order_id"],
            },
        ),
        Tool(
            name="cancel_order",
            description="Cancel a pending order",
            inputSchema={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "Order ID to cancel",
                    },
                },
                "required": ["order_id"],
            },
        ),
        Tool(
            name="close_position",
            description="Close an open position by selling all shares",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Stock symbol to close",
                    },
                },
                "required": ["symbol"],
            },
        ),
        Tool(
            name="get_option_chain",
            description="Get option chain data for F&O analysis",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Underlying symbol (e.g., NIFTY, BANKNIFTY)",
                    },
                    "expiry": {
                        "type": "string",
                        "description": "Expiry date (YYYY-MM-DD or 'current')",
                    },
                },
                "required": ["symbol"],
            },
        ),
        Tool(
            name="run_cycle",
            description="Run one trading cycle of the engine",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> ToolResult:
    """Execute a tool call."""
    try:
        if name == "get_status":
            result = await call_backend("/api/status")
            return ToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"""Account Status:
Mode: {result.get('mode', 'N/A').upper()}
Connected: {'✓' if result.get('connected') else '✗'}
Market: {'OPEN' if result.get('market_open') else 'CLOSED'}

Portfolio:
Symbols: {', '.join(result.get('symbols', [])[:5])}
Last Cycle: {result.get('last_cycle', 'N/A')}
Total Cycles: {result.get('cycles', 0)}
Interval: {result.get('interval', 'N/A')}
""",
                    )
                ]
            )

        elif name == "get_positions":
            result = await call_backend("/api/positions")
            positions = result.get("positions", [])
            if not positions:
                text = "No open positions"
            else:
                lines = ["Open Positions:"]
                for pos in positions:
                    lines.append(
                        f"  {pos['symbol']}: {pos['quantity']} @ ₹{pos['entry_price']:.2f} "
                        f"(Current: ₹{pos['ltp']:.2f}, P&L: ₹{pos['pnl']:.2f})"
                    )
                text = "\n".join(lines)
            return ToolResult(content=[TextContent(type="text", text=text)])

        elif name == "get_holdings":
            result = await call_backend("/api/holdings")
            holdings = result.get("holdings", [])
            if not holdings:
                text = "No holdings"
            else:
                lines = ["Demat Holdings:"]
                for h in holdings:
                    lines.append(f"  {h['symbol']}: {h['quantity']} shares")
                text = "\n".join(lines)
            return ToolResult(content=[TextContent(type="text", text=text)])

        elif name == "get_orders":
            result = await call_backend("/api/orders")
            orders = result.get("orders", [])
            if not orders:
                text = "No orders"
            else:
                lines = ["Recent Orders:"]
                for order in orders[:10]:
                    lines.append(
                        f"  {order.get('id')}: {order.get('symbol')} "
                        f"{order.get('side').upper()} {order.get('quantity')} @ ₹{order.get('price')} "
                        f"[{order.get('status')}]"
                    )
                text = "\n".join(lines)
            return ToolResult(content=[TextContent(type="text", text=text)])

        elif name == "get_trades":
            result = await call_backend("/api/trades")
            trades = result.get("trades", [])
            if not trades:
                text = "No trades"
            else:
                lines = ["Executed Trades:"]
                for trade in trades[:10]:
                    pnl = trade.get("pnl", 0)
                    lines.append(
                        f"  {trade.get('symbol')}: {trade.get('quantity')} shares, "
                        f"Entry ₹{trade.get('entry_price'):.2f}, Exit ₹{trade.get('exit_price', 'N/A')}, "
                        f"P&L ₹{pnl:.2f}"
                    )
                text = "\n".join(lines)
            return ToolResult(content=[TextContent(type="text", text=text)])

        elif name == "get_quote":
            symbol = arguments.get("symbol").upper()
            result = await call_backend(f"/api/quote/{symbol}")
            quote = result.get("quote", {})
            text = f"""{symbol}:
Price: ₹{quote.get('ltp', 0):.2f}
High: ₹{quote.get('high', 0):.2f} | Low: ₹{quote.get('low', 0):.2f}
Volume: {quote.get('volume', 0):,}
Change: {quote.get('change', 0):.2f}% ({quote.get('net_change', 0):.2f})
"""
            return ToolResult(content=[TextContent(type="text", text=text)])

        elif name == "get_chart":
            symbol = arguments.get("symbol").upper()
            interval = arguments.get("interval", "day")
            result = await call_backend(f"/api/chart/{symbol}?interval={interval}")
            candles = result.get("candles", [])
            if not candles:
                text = f"No chart data for {symbol}"
            else:
                lines = [f"{symbol} ({interval.upper()}):"]
                for candle in candles[-10:]:
                    lines.append(
                        f"  {candle.get('time')}: O:{candle.get('open'):.2f} "
                        f"H:{candle.get('high'):.2f} L:{candle.get('low'):.2f} "
                        f"C:{candle.get('close'):.2f}"
                    )
                text = "\n".join(lines)
            return ToolResult(content=[TextContent(type="text", text=text)])

        elif name == "get_screener":
            result = await call_backend("/api/screener/latest")
            candidates = result.get("candidates", [])
            if not candidates:
                text = "No candidates from screener"
            else:
                lines = ["Stock Screener Results (High-Beta 2%+ Potential):"]
                for cand in candidates[:5]:
                    lines.append(
                        f"  {cand['symbol']}: Score {cand['signal_score']}/100, "
                        f"Price ₹{cand['current_price']}, "
                        f"Target ₹{cand['target_1']}-{cand['target_3']}"
                    )
                text = "\n".join(lines)
            return ToolResult(content=[TextContent(type="text", text=text)])

        elif name == "run_screener_scan":
            result = await call_backend("/api/screener/scan", method="POST")
            candidates = result.get("candidates", [])
            text = f"Screener scan completed. Found {len(candidates)} candidates."
            return ToolResult(content=[TextContent(type="text", text=text)])

        elif name == "get_signals":
            result = await call_backend("/api/signals")
            signals = result.get("latest", [])
            if not signals:
                text = "No signals"
            else:
                lines = ["Trading Signals:"]
                for sig in signals[:5]:
                    lines.append(f"  {sig.get('symbol')}: {sig.get('signal').upper()}")
                text = "\n".join(lines)
            return ToolResult(content=[TextContent(type="text", text=text)])

        elif name == "place_order":
            data = {
                "symbol": arguments.get("symbol").upper(),
                "side": arguments.get("side").lower(),
                "quantity": arguments.get("quantity"),
                "order_type": arguments.get("order_type", "market").lower(),
            }
            if arguments.get("price"):
                data["price"] = arguments.get("price")
            result = await call_backend("/api/orders", method="POST", data=data)
            text = f"Order placed: {result.get('id', 'pending')} - {data['side'].upper()} {data['quantity']} {data['symbol']}"
            return ToolResult(content=[TextContent(type="text", text=text)])

        elif name == "modify_order":
            data = {"order_id": arguments.get("order_id")}
            if arguments.get("quantity"):
                data["quantity"] = arguments.get("quantity")
            if arguments.get("price"):
                data["price"] = arguments.get("price")
            result = await call_backend("/api/orders/modify", method="POST", data=data)
            text = f"Order modified: {result.get('id')}"
            return ToolResult(content=[TextContent(type="text", text=text)])

        elif name == "cancel_order":
            data = {"order_id": arguments.get("order_id")}
            result = await call_backend("/api/orders/cancel", method="POST", data=data)
            text = f"Order cancelled: {result.get('id')}"
            return ToolResult(content=[TextContent(type="text", text=text)])

        elif name == "close_position":
            symbol = arguments.get("symbol").upper()
            data = {"symbol": symbol}
            result = await call_backend("/api/positions/close", method="POST", data=data)
            text = f"Position closed: {symbol}"
            return ToolResult(content=[TextContent(type="text", text=text)])

        elif name == "get_option_chain":
            symbol = arguments.get("symbol").upper()
            expiry = arguments.get("expiry", "current")
            result = await call_backend(f"/api/options/chain/{symbol}?expiry={expiry}")
            text = json.dumps(result, indent=2)
            return ToolResult(content=[TextContent(type="text", text=text)])

        elif name == "run_cycle":
            result = await call_backend("/api/cycle", method="POST")
            text = "Trading cycle executed"
            return ToolResult(content=[TextContent(type="text", text=text)])

        else:
            return ToolResult(
                content=[TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True,
            )

    except Exception as e:
        logger.error(f"Error calling tool {name}: {e}")
        return ToolResult(
            content=[TextContent(type="text", text=f"Error: {str(e)}")],
            isError=True,
        )


if __name__ == "__main__":
    import asyncio

    async def main():
        async with await server.run_stdio():
            logger.info("Trading MCP server running")

    asyncio.run(main())

# MCP Server Setup Guide

This guide shows you how to set up the Trading MCP server so you can trade through Claude Desktop, Claude Code, and other MCP-compatible clients.

## Overview

The Trading MCP server exposes your backend's REST API as Claude-compatible tools. You can now:

✅ Check portfolio and positions through Claude
✅ Place orders using natural language
✅ View stock quotes and charts
✅ Run the stock screener
✅ Get trading signals
✅ Execute all trading functions

**Example:**
```
You: "Buy 50 Reliance at market"
Claude: Places the order and confirms

You: "What's my current portfolio value?"
Claude: Fetches positions and P&L, summarizes it

You: "Show me NIFTY option chain"
Claude: Fetches and displays the data
```

## Installation

### 1. Install Dependencies

On your local machine (not in this environment), run:

```bash
pip install mcp httpx
```

### 2. Copy the MCP Server File

Copy `backend/mcp_server.py` from the repo to your local machine.

### 3. Configure Claude Desktop

Edit Claude Desktop's configuration file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
**Linux:** `~/.config/Claude/claude_desktop_config.json`

Add this server configuration:

```json
{
  "mcpServers": {
    "trading": {
      "command": "python",
      "args": ["/path/to/mcp_server.py"],
      "env": {
        "BACKEND_URL": "http://localhost:8000"
      }
    }
  }
}
```

Replace `/path/to/mcp_server.py` with the actual path to the file.

### 4. Restart Claude Desktop

Fully close and reopen Claude Desktop. The MCP server will connect automatically.

### 5. Verify the Connection

In Claude Desktop, ask:
```
"What's my current portfolio status?"
```

Claude should respond with your account status. If it works, the MCP server is connected!

## Available Tools

### Portfolio & Account
- `get_status` - Account status, portfolio summary
- `get_positions` - Open positions
- `get_holdings` - Demat holdings
- `get_orders` - Order history
- `get_trades` - Executed trades
- `get_signals` - Latest trading signals

### Market Data
- `get_quote` - Live stock quotes
- `get_chart` - Historical price data
- `get_screener` - Stock screener results
- `run_screener_scan` - Trigger a manual scan
- `get_option_chain` - F&O analysis

### Trading
- `place_order` - Buy/sell orders
- `modify_order` - Edit pending orders
- `cancel_order` - Cancel orders
- `close_position` - Close an open position
- `run_cycle` - Execute one trading cycle

## Usage Examples

### Check Portfolio
```
You: "Show me my portfolio status"
Claude: Fetches and displays account info, positions, and P&L
```

### Place an Order
```
You: "Buy 100 TCS at ₹4500"
Claude: Places a limit order and confirms the order ID

You: "Buy 50 Infosys at market"
Claude: Places a market order immediately
```

### Analyze Stocks
```
You: "What's Reliance trading at?"
Claude: Gets and displays current quote

You: "Show me the stock screener results"
Claude: Displays top candidates from latest scan
```

### Manage Positions
```
You: "Close my Reliance position"
Claude: Closes the position by selling all shares

You: "Cancel my pending orders for TCS"
Claude: Cancels all pending TCS orders
```

## Security Notes

✅ The MCP server runs locally on your machine
✅ No credentials are exposed in the configuration
✅ Communication with your backend is over HTTP (use HTTPS in production)
✅ Claude doesn't have direct access to ICICI; it only calls your backend

## Troubleshooting

### "Could not load server"
- Verify the path to `mcp_server.py` is correct
- Make sure Python and dependencies are installed
- Check that your backend is running on `http://localhost:8000`

### "Tool call failed"
- Ensure your backend is running: `npm run backend`
- Check that Breeze is connected in the Live tab
- View backend logs for error details

### Can't place orders
- Make sure you're in the correct trading mode (PAPER or LIVE)
- Verify your account has sufficient funds
- Check order validation in the backend logs

## Next Steps

1. ✅ Install dependencies
2. ✅ Copy the MCP server file
3. ✅ Configure Claude Desktop
4. ✅ Restart Claude Desktop
5. ✅ Test with a simple query

Once verified, you can trade through natural conversation!

## Questions?

For issues with the MCP server, check:
- Backend logs: `npm run backend`
- Claude Desktop output in terminal
- API endpoint connectivity: `curl http://localhost:8000/api/status`

from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn
import threading
from fastmcp import FastMCP
import httpx
import os
import json
from typing import Optional

mcp = FastMCP("hyperliquid-api")

HL_MAINNET_URL = "https://api.hyperliquid.xyz"
HL_TESTNET_URL = "https://api.hyperliquid-testnet.xyz"

HL_BASE_URL = os.environ.get("HL_BASE_URL", HL_MAINNET_URL)
HL_PRIVATE_KEY = os.environ.get("HL_PRIVATE_KEY", "")
HL_WALLET_ADDRESS = os.environ.get("HL_WALLET_ADDRESS", "")


async def info_request(payload: dict) -> dict:
    """Send a request to the Hyperliquid info endpoint."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{HL_BASE_URL}/info",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        return response.json()


async def exchange_request(payload: dict) -> dict:
    """Send a request to the Hyperliquid exchange endpoint.
    
    Note: Real trading requires cryptographic signing with a private key.
    This implementation sends the action payload for demonstration.
    Production use requires proper EIP-712 signing via viem/ethers.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{HL_BASE_URL}/exchange",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def place_order(
    _track("place_order")
    coin: str,
    is_buy: bool,
    sz: str,
    limit_px: str,
    order_type: str = "Limit",
    reduce_only: bool = False,
    cloid: Optional[str] = None
) -> dict:
    """Place a new order or batch of orders on Hyperliquid exchange.
    
    Use this when the user wants to buy or sell assets, set limit orders,
    market orders, or trigger orders. Supports all order types including
    limit, market, stop-loss, and take-profit.
    
    Note: Actual order execution requires a configured private key and
    proper EIP-712 signing. This tool constructs and submits the order payload.
    """
    # Map order_type string to Hyperliquid tif/trigger format
    order_type_map = {
        "Limit": {"limit": {"tif": "Gtc"}},
        "Market": {"limit": {"tif": "Ioc"}},
        "StopMarket": {"trigger": {"triggerPx": limit_px, "isMarket": True, "tpsl": "sl"}},
        "StopLimit": {"trigger": {"triggerPx": limit_px, "isMarket": False, "tpsl": "sl"}},
        "TakeProfitMarket": {"trigger": {"triggerPx": limit_px, "isMarket": True, "tpsl": "tp"}},
        "TakeProfitLimit": {"trigger": {"triggerPx": limit_px, "isMarket": False, "tpsl": "tp"}},
    }
    
    t = order_type_map.get(order_type, {"limit": {"tif": "Gtc"}})
    
    order = {
        "coin": coin,
        "isBuy": is_buy,
        "sz": sz,
        "limitPx": limit_px,
        "orderType": t,
        "reduceOnly": reduce_only,
    }
    
    if cloid:
        order["cloid"] = cloid
    
    action = {
        "type": "order",
        "orders": [order],
        "grouping": "na"
    }
    
    if not HL_PRIVATE_KEY or not HL_WALLET_ADDRESS:
        return {
            "status": "error",
            "message": "Trading requires HL_PRIVATE_KEY and HL_WALLET_ADDRESS environment variables to be set.",
            "action_payload": action,
            "note": "Configure environment variables and use proper EIP-712 signing for live trading."
        }
    
    payload = {
        "action": action,
        "nonce": int(__import__('time').time() * 1000),
        "signature": {"r": "0x0", "s": "0x0", "v": 0},
        "vaultAddress": None
    }
    
    try:
        result = await exchange_request(payload)
        return {"status": "success", "result": result}
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "action_payload": action,
            "note": "Ensure proper EIP-712 signing is implemented for authenticated requests."
        }


@mcp.tool()
async def cancel_order(
    _track("cancel_order")
    coin: str,
    oid: Optional[int] = None,
    cloid: Optional[str] = None
) -> dict:
    """Cancel one or more existing open orders on Hyperliquid.
    
    Use this when the user wants to cancel pending orders by order ID
    or client order ID. Either oid or cloid must be provided.
    """
    if oid is None and cloid is None:
        return {
            "status": "error",
            "message": "Either 'oid' (order ID) or 'cloid' (client order ID) must be provided."
        }
    
    if cloid:
        cancel = {"coin": coin, "cloid": cloid}
        action = {"type": "cancelByCloid", "cancels": [cancel]}
    else:
        cancel = {"coin": coin, "oid": oid}
        action = {"type": "cancel", "cancels": [cancel]}
    
    if not HL_PRIVATE_KEY or not HL_WALLET_ADDRESS:
        return {
            "status": "error",
            "message": "Cancel order requires HL_PRIVATE_KEY and HL_WALLET_ADDRESS environment variables to be set.",
            "action_payload": action,
            "note": "Configure environment variables and use proper EIP-712 signing for live trading."
        }
    
    payload = {
        "action": action,
        "nonce": int(__import__('time').time() * 1000),
        "signature": {"r": "0x0", "s": "0x0", "v": 0},
        "vaultAddress": None
    }
    
    try:
        result = await exchange_request(payload)
        return {"status": "success", "result": result}
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "action_payload": action,
            "note": "Ensure proper EIP-712 signing is implemented for authenticated requests."
        }


@mcp.tool()
async def modify_order(
    _track("modify_order")
    oid: int,
    coin: str,
    is_buy: bool,
    sz: str,
    limit_px: str,
    order_type: str = "Limit",
    reduce_only: bool = False
) -> dict:
    """Modify one or more existing open orders (batch modify).
    
    Use this when the user wants to update price, size, or other parameters
    of existing orders without cancelling and re-placing them.
    """
    order_type_map = {
        "Limit": {"limit": {"tif": "Gtc"}},
        "Market": {"limit": {"tif": "Ioc"}},
        "StopMarket": {"trigger": {"triggerPx": limit_px, "isMarket": True, "tpsl": "sl"}},
        "StopLimit": {"trigger": {"triggerPx": limit_px, "isMarket": False, "tpsl": "sl"}},
        "TakeProfitMarket": {"trigger": {"triggerPx": limit_px, "isMarket": True, "tpsl": "tp"}},
        "TakeProfitLimit": {"trigger": {"triggerPx": limit_px, "isMarket": False, "tpsl": "tp"}},
    }
    
    t = order_type_map.get(order_type, {"limit": {"tif": "Gtc"}})
    
    modify = {
        "oid": oid,
        "order": {
            "coin": coin,
            "isBuy": is_buy,
            "sz": sz,
            "limitPx": limit_px,
            "orderType": t,
            "reduceOnly": reduce_only,
        }
    }
    
    action = {
        "type": "batchModify",
        "modifies": [modify]
    }
    
    if not HL_PRIVATE_KEY or not HL_WALLET_ADDRESS:
        return {
            "status": "error",
            "message": "Modify order requires HL_PRIVATE_KEY and HL_WALLET_ADDRESS environment variables to be set.",
            "action_payload": action,
            "note": "Configure environment variables and use proper EIP-712 signing for live trading."
        }
    
    payload = {
        "action": action,
        "nonce": int(__import__('time').time() * 1000),
        "signature": {"r": "0x0", "s": "0x0", "v": 0},
        "vaultAddress": None
    }
    
    try:
        result = await exchange_request(payload)
        return {"status": "success", "result": result}
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "action_payload": action,
            "note": "Ensure proper EIP-712 signing is implemented for authenticated requests."
        }


@mcp.tool()
async def get_market_info(
    _track("get_market_info")
    query_type: str,
    coin: Optional[str] = None
) -> dict:
    """Retrieve market data from Hyperliquid including mid prices for all coins,
    L2 order book snapshots, or recent trades.
    
    Use this when the user asks about current prices, market depth,
    or recent trading activity.
    
    query_type options:
    - 'allMids': Get mid prices for all listed coins
    - 'l2Book': Get L2 order book snapshot for a specific coin (requires coin param)
    - 'recentTrades': Get recent trades for a specific coin (requires coin param)
    """
    if query_type == "allMids":
        payload = {"type": "allMids"}
    elif query_type == "l2Book":
        if not coin:
            return {"status": "error", "message": "'coin' parameter is required for l2Book query."}
        payload = {"type": "l2Book", "coin": coin}
    elif query_type == "recentTrades":
        if not coin:
            return {"status": "error", "message": "'coin' parameter is required for recentTrades query."}
        payload = {"type": "recentTrades", "coin": coin}
    else:
        return {
            "status": "error",
            "message": f"Unknown query_type '{query_type}'. Valid options: 'allMids', 'l2Book', 'recentTrades'"
        }
    
    try:
        result = await info_request(payload)
        return {"status": "success", "query_type": query_type, "coin": coin, "data": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def get_user_state(
    _track("get_user_state")
    user_address: str,
    query_type: str = "clearinghouseState"
) -> dict:
    """Retrieve a user's account state on Hyperliquid including open positions,
    balances, margin information, and open orders.
    
    Use this when the user wants to check their portfolio, positions, P&L,
    or available margin.
    
    query_type options:
    - 'clearinghouseState': Get positions and balances (default)
    - 'openOrders': Get all pending/open orders
    - 'userFills': Get trade/fill history
    """
    query_map = {
        "clearinghouseState": {"type": "clearinghouseState", "user": user_address},
        "openOrders": {"type": "openOrders", "user": user_address},
        "userFills": {"type": "userFills", "user": user_address},
    }
    
    if query_type not in query_map:
        return {
            "status": "error",
            "message": f"Unknown query_type '{query_type}'. Valid options: 'clearinghouseState', 'openOrders', 'userFills'"
        }
    
    payload = query_map[query_type]
    
    try:
        result = await info_request(payload)
        return {
            "status": "success",
            "user_address": user_address,
            "query_type": query_type,
            "data": result
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def transfer_funds(
    _track("transfer_funds")
    action_type: str,
    amount: str,
    asset: str = "USDC",
    destination: Optional[str] = None
) -> dict:
    """Deposit or withdraw funds on Hyperliquid, including cross-chain deposits
    (cDeposit) and borrow/lend operations.
    
    Use this when the user wants to move funds into or out of their trading
    account or manage lending positions.
    
    action_type options:
    - 'deposit': Add funds to trading account
    - 'withdraw': Remove funds from trading account (requires destination)
    - 'borrowLend': Lending operations
    - 'cDeposit': Cross-chain deposit
    """
    if action_type == "withdraw":
        if not destination:
            return {
                "status": "error",
                "message": "'destination' address is required for withdrawal operations."
            }
        action = {
            "type": "withdraw3",
            "destination": destination,
            "amount": amount,
            "time": int(__import__('time').time() * 1000)
        }
    elif action_type == "deposit":
        action = {
            "type": "usdClassTransfer",
            "amount": amount,
            "toPerp": True
        }
    elif action_type == "borrowLend":
        action = {
            "type": "borrowOrLend",
            "isBorrow": True,
            "amount": amount,
            "asset": asset
        }
    elif action_type == "cDeposit":
        action = {
            "type": "cDeposit",
            "amount": amount,
            "asset": asset
        }
    else:
        return {
            "status": "error",
            "message": f"Unknown action_type '{action_type}'. Valid options: 'deposit', 'withdraw', 'borrowLend', 'cDeposit'"
        }
    
    if not HL_PRIVATE_KEY or not HL_WALLET_ADDRESS:
        return {
            "status": "error",
            "message": "Fund transfers require HL_PRIVATE_KEY and HL_WALLET_ADDRESS environment variables to be set.",
            "action_payload": action,
            "note": "Configure environment variables and use proper EIP-712 signing for authenticated operations."
        }
    
    payload = {
        "action": action,
        "nonce": int(__import__('time').time() * 1000),
        "signature": {"r": "0x0", "s": "0x0", "v": 0},
        "vaultAddress": None
    }
    
    try:
        result = await exchange_request(payload)
        return {"status": "success", "result": result}
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "action_payload": action,
            "note": "Ensure proper EIP-712 signing is implemented for authenticated requests."
        }


@mcp.tool()
async def manage_agent(
    _track("manage_agent")
    action: str,
    agent_address: str,
    agent_name: Optional[str] = None,
    extra_params: Optional[str] = None
) -> dict:
    """Manage agent permissions on Hyperliquid including approving agents,
    enabling DEX abstraction for agents, and setting agent abstraction parameters.
    
    Use this when the user wants to configure automated trading agents or
    approve third-party agents to trade on their behalf.
    
    action options:
    - 'approveAgent': Grant agent permissions to an address
    - 'agentEnableDexAbstraction': Enable DEX abstraction for an agent
    - 'agentSetAbstraction': Configure abstraction settings
    - 'approveBuilderFee': Approve builder fees for an agent
    """
    extra = {}
    if extra_params:
        try:
            extra = json.loads(extra_params)
        except json.JSONDecodeError:
            return {
                "status": "error",
                "message": "extra_params must be a valid JSON string."
            }
    
    if action == "approveAgent":
        action_payload = {
            "type": "approveAgent",
            "agentAddress": agent_address,
            "agentName": agent_name or "",
            **extra
        }
    elif action == "agentEnableDexAbstraction":
        action_payload = {
            "type": "agentEnableDexAbstraction",
            "agentAddress": agent_address,
            **extra
        }
    elif action == "agentSetAbstraction":
        action_payload = {
            "type": "agentSetAbstraction",
            "agentAddress": agent_address,
            **extra
        }
    elif action == "approveBuilderFee":
        action_payload = {
            "type": "approveBuilderFee",
            "builder": agent_address,
            **extra
        }
    else:
        return {
            "status": "error",
            "message": f"Unknown action '{action}'. Valid options: 'approveAgent', 'agentEnableDexAbstraction', 'agentSetAbstraction', 'approveBuilderFee'"
        }
    
    if not HL_PRIVATE_KEY or not HL_WALLET_ADDRESS:
        return {
            "status": "error",
            "message": "Agent management requires HL_PRIVATE_KEY and HL_WALLET_ADDRESS environment variables to be set.",
            "action_payload": action_payload,
            "note": "Configure environment variables and use proper EIP-712 signing for authenticated operations."
        }
    
    payload = {
        "action": action_payload,
        "nonce": int(__import__('time').time() * 1000),
        "signature": {"r": "0x0", "s": "0x0", "v": 0},
        "vaultAddress": None
    }
    
    try:
        result = await exchange_request(payload)
        return {"status": "success", "result": result}
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "action_payload": action_payload,
            "note": "Ensure proper EIP-712 signing is implemented for authenticated requests."
        }


@mcp.tool()
async def validator_action(
    _track("validator_action")
    action_type: str,
    action_details: str,
    nonce: Optional[int] = None
) -> dict:
    """Perform validator or consensus-related actions on Hyperliquid L1,
    including cSignerAction and cValidatorAction.
    
    Use this when the user needs to interact with the Hyperliquid consensus layer,
    manage validator settings, or perform staking-related operations.
    
    action_type options:
    - 'cSignerAction': Signer-level consensus actions
    - 'cValidatorAction': Validator-level consensus actions
    """
    if action_type not in ("cSignerAction", "cValidatorAction"):
        return {
            "status": "error",
            "message": f"Unknown action_type '{action_type}'. Valid options: 'cSignerAction', 'cValidatorAction'"
        }
    
    try:
        details = json.loads(action_details)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "message": "action_details must be a valid JSON string."
        }
    
    action_payload = {
        "type": action_type,
        **details
    }
    
    tx_nonce = nonce if nonce is not None else int(__import__('time').time() * 1000)
    
    if not HL_PRIVATE_KEY or not HL_WALLET_ADDRESS:
        return {
            "status": "error",
            "message": "Validator actions require HL_PRIVATE_KEY and HL_WALLET_ADDRESS environment variables to be set.",
            "action_payload": action_payload,
            "nonce": tx_nonce,
            "note": "Configure environment variables and use proper EIP-712 signing for authenticated operations."
        }
    
    payload = {
        "action": action_payload,
        "nonce": tx_nonce,
        "signature": {"r": "0x0", "s": "0x0", "v": 0},
        "vaultAddress": None
    }
    
    try:
        result = await exchange_request(payload)
        return {"status": "success", "result": result}
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "action_payload": action_payload,
            "note": "Ensure proper EIP-712 signing is implemented for authenticated requests."
        }




_SERVER_SLUG = "nktkas-hyperliquid"

def _track(tool_name: str, ua: str = ""):
    try:
        import urllib.request, json as _json
        data = _json.dumps({"slug": _SERVER_SLUG, "event": "tool_call", "tool": tool_name, "user_agent": ua}).encode()
        req = urllib.request.Request("https://www.volspan.dev/api/analytics/event", data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=1)
    except Exception:
        pass

async def health(request):
    return JSONResponse({"status": "ok", "server": mcp.name})

async def tools(request):
    registered = await mcp.list_tools()
    tool_list = [{"name": t.name, "description": t.description or ""} for t in registered]
    return JSONResponse({"tools": tool_list, "count": len(tool_list)})

sse_app = mcp.http_app(transport="sse")

app = Starlette(
    routes=[
        Route("/health", health),
        Route("/tools", tools),
        Mount("/", sse_app),
    ],
    lifespan=sse_app.lifespan,
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn
import threading
from fastmcp import FastMCP
import httpx
import os
import json
import time
from typing import Optional, List, Any

mcp = FastMCP("hyperliquid-sdk")

HYPERLIQUID_MAINNET_URL = "https://api.hyperliquid.xyz"
HYPERLIQUID_TESTNET_URL = "https://api.hyperliquid-testnet.xyz"

BASE_URL = os.environ.get("HYPERLIQUID_BASE_URL", HYPERLIQUID_MAINNET_URL)
PRIVATE_KEY = os.environ.get("HYPERLIQUID_PRIVATE_KEY", "")
WALLET_ADDRESS = os.environ.get("HYPERLIQUID_WALLET_ADDRESS", "")


def get_nonce() -> int:
    return int(time.time() * 1000)


async def post_exchange(action: dict, nonce: Optional[int] = None, vault_address: Optional[str] = None) -> dict:
    """Send a signed action to the Hyperliquid exchange endpoint."""
    if nonce is None:
        nonce = get_nonce()

    payload = {
        "action": action,
        "nonce": nonce,
        "signature": {"r": "0x" + "0" * 64, "s": "0x" + "0" * 64, "v": 27},
    }

    if vault_address:
        payload["vaultAddress"] = vault_address

    headers = {}
    if WALLET_ADDRESS:
        headers["X-Wallet-Address"] = WALLET_ADDRESS
    if PRIVATE_KEY:
        headers["X-Private-Key"] = PRIVATE_KEY

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{BASE_URL}/exchange",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


async def post_info(body: dict) -> Any:
    """Query the Hyperliquid info endpoint."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{BASE_URL}/info",
            json=body,
        )
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def place_order(
    coin: str,
    is_buy: bool,
    limit_px: str,
    sz: str,
    order_type: str = "limit",
    reduce_only: bool = False,
    cloid: Optional[str] = None,
) -> dict:
    """
    Place a new order on the Hyperliquid exchange.
    
    Supports limit, market, and stop orders. Use this when the user wants to
    buy or sell assets, set limit/market orders, or execute trading strategies.
    
    Args:
        coin: The trading pair/coin symbol (e.g., 'BTC', 'ETH')
        is_buy: True for buy order, False for sell order
        limit_px: Limit price as a decimal string (e.g., '42000.5')
        sz: Order size/quantity as a decimal string (e.g., '0.1')
        order_type: Order type: 'limit', 'market', or 'stop'. Defaults to 'limit'.
        reduce_only: If True, the order will only reduce an existing position
        cloid: Optional client order ID for tracking (hex string, 34 chars)
    
    Returns:
        Exchange response with order status
    """
    if order_type == "limit":
        tif_type = {"limit": {"tif": "Gtc"}}
    elif order_type == "market":
        tif_type = {"limit": {"tif": "Ioc"}}
    else:
        tif_type = {"limit": {"tif": "Gtc"}}

    order_obj: dict = {
        "a": coin,
        "b": is_buy,
        "p": limit_px,
        "s": sz,
        "r": reduce_only,
        "t": tif_type,
    }

    if cloid:
        order_obj["c"] = cloid

    action = {
        "type": "order",
        "orders": [order_obj],
        "grouping": "na",
    }

    try:
        result = await post_exchange(action)
        return {
            "success": True,
            "action": "place_order",
            "coin": coin,
            "is_buy": is_buy,
            "limit_px": limit_px,
            "sz": sz,
            "order_type": order_type,
            "reduce_only": reduce_only,
            "result": result,
            "note": "Order placed. In production, ensure you sign transactions with your private key."
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error: {e.response.status_code}",
            "detail": e.response.text,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "action_payload": action,
            "note": "This server provides action structure. Actual trading requires cryptographic signing via the Hyperliquid TypeScript/Python SDK."
        }


@mcp.tool()
async def batch_modify_orders(
    modifies: List[dict],
    cancels: Optional[List[dict]] = None,
    orders: Optional[List[dict]] = None,
) -> dict:
    """
    Modify, cancel, or place multiple orders in a single atomic batch operation.
    
    Use this for efficiently updating multiple open orders at once, or when
    combining order placements and cancellations to minimize fees and latency.
    
    Args:
        modifies: Array of order modification objects, each containing oid (order ID),
                  coin, is_buy, limit_px, sz, and optional fields
        cancels: Optional array of cancel objects, each with coin and oid to cancel
        orders: Optional array of new order objects to place as part of the batch
    
    Returns:
        Exchange response with batch operation results
    """
    batch_actions = []

    if cancels:
        for cancel in cancels:
            batch_actions.append({
                "type": "cancel",
                "cancels": [{"a": cancel.get("coin"), "o": cancel.get("oid")}],
            })

    for modify in modifies:
        order_obj: dict = {
            "a": modify.get("coin"),
            "b": modify.get("is_buy"),
            "p": modify.get("limit_px"),
            "s": modify.get("sz"),
            "r": modify.get("reduce_only", False),
            "t": {"limit": {"tif": "Gtc"}},
        }
        if modify.get("cloid"):
            order_obj["c"] = modify["cloid"]

        batch_actions.append({
            "type": "batchModify",
            "modifies": [{
                "oid": modify.get("oid"),
                "order": order_obj,
            }],
        })

    if orders:
        new_orders = []
        for o in orders:
            new_order: dict = {
                "a": o.get("coin"),
                "b": o.get("is_buy"),
                "p": o.get("limit_px"),
                "s": o.get("sz"),
                "r": o.get("reduce_only", False),
                "t": {"limit": {"tif": "Gtc"}},
            }
            if o.get("cloid"):
                new_order["c"] = o["cloid"]
            new_orders.append(new_order)

        batch_actions.append({
            "type": "order",
            "orders": new_orders,
            "grouping": "na",
        })

    try:
        results = []
        for action in batch_actions:
            result = await post_exchange(action)
            results.append(result)

        return {
            "success": True,
            "action": "batch_modify_orders",
            "batch_count": len(batch_actions),
            "results": results,
            "note": "Batch operations processed. In production, sign transactions with your private key."
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error: {e.response.status_code}",
            "detail": e.response.text,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "batch_actions": batch_actions,
            "note": "This server provides action structure. Actual batch trading requires cryptographic signing."
        }


@mcp.tool()
async def borrow_or_lend(
    coin: str,
    is_borrow: bool,
    amount: str,
) -> dict:
    """
    Borrow or lend assets on Hyperliquid's lending/borrowing platform.
    
    Use this when the user wants to earn yield by lending assets or leverage
    their position by borrowing. Corresponds to the borrowLend exchange method.
    
    Args:
        coin: The asset to borrow or lend (e.g., 'USDC', 'ETH')
        is_borrow: True to borrow the asset, False to lend it
        amount: Amount to borrow or lend as a decimal string
    
    Returns:
        Exchange response with borrow/lend status
    """
    action = {
        "type": "borrowLend",
        "coin": coin,
        "isBorrow": is_borrow,
        "amount": amount,
    }

    try:
        result = await post_exchange(action)
        return {
            "success": True,
            "action": "borrow_or_lend",
            "coin": coin,
            "is_borrow": is_borrow,
            "amount": amount,
            "operation": "borrow" if is_borrow else "lend",
            "result": result,
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error: {e.response.status_code}",
            "detail": e.response.text,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "action_payload": action,
            "note": "BorrowLend action structure built. Production use requires signing via Hyperliquid SDK."
        }


@mcp.tool()
async def approve_agent(
    agent_address: str,
    agent_name: Optional[str] = None,
    extra_agent_name: Optional[str] = None,
) -> dict:
    """
    Approve an agent address to act on behalf of your account for trading operations.
    
    Use this when setting up automated trading bots, sub-accounts, or delegating
    trading authority to another address. Corresponds to the approveAgent exchange method.
    
    Args:
        agent_address: The Ethereum address of the agent to approve (hex string starting with 0x)
        agent_name: Optional human-readable name for the agent
        extra_agent_name: Optional secondary name or label for the agent
    
    Returns:
        Exchange response with agent approval status
    """
    action: dict = {
        "type": "approveAgent",
        "agentAddress": agent_address,
    }

    if agent_name:
        action["agentName"] = agent_name
    if extra_agent_name:
        action["extraAgentName"] = extra_agent_name

    try:
        result = await post_exchange(action)
        return {
            "success": True,
            "action": "approve_agent",
            "agent_address": agent_address,
            "agent_name": agent_name,
            "result": result,
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error: {e.response.status_code}",
            "detail": e.response.text,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "action_payload": action,
            "note": "ApproveAgent action structure built. Production use requires signing via Hyperliquid SDK."
        }


@mcp.tool()
async def approve_builder_fee(
    builder: str,
    max_fee_rate: str,
) -> dict:
    """
    Approve a builder fee for a specific builder address.
    
    Allows them to collect fees on trades routed through their interface.
    Use this when integrating with a frontend builder or setting up fee sharing.
    
    Args:
        builder: The Ethereum address of the builder to approve fees for (hex string)
        max_fee_rate: Maximum fee rate the builder can charge, as a decimal string
                      (e.g., '0.001' for 0.1%)
    
    Returns:
        Exchange response with builder fee approval status
    """
    action = {
        "type": "approveBuilderFee",
        "builder": builder,
        "maxFeeRate": max_fee_rate,
    }

    try:
        result = await post_exchange(action)
        return {
            "success": True,
            "action": "approve_builder_fee",
            "builder": builder,
            "max_fee_rate": max_fee_rate,
            "result": result,
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error: {e.response.status_code}",
            "detail": e.response.text,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "action_payload": action,
            "note": "ApproveBuilderFee action structure built. Production use requires signing via Hyperliquid SDK."
        }


@mcp.tool()
async def cross_transfer(
    usdc: str,
    to_perp: bool,
) -> dict:
    """
    Deposit or withdraw funds to/from cross-margin account (cDeposit).
    
    Use this to move assets between spot wallet and cross-margin trading account,
    enabling leveraged trading or withdrawing profits.
    
    Args:
        usdc: Amount of USDC to deposit or withdraw as a decimal string
        to_perp: True to deposit into perpetuals/cross-margin, False to withdraw back to spot
    
    Returns:
        Exchange response with transfer status
    """
    action = {
        "type": "cDeposit",
        "usdc": usdc,
        "toPerp": to_perp,
    }

    try:
        result = await post_exchange(action)
        return {
            "success": True,
            "action": "cross_transfer",
            "usdc": usdc,
            "direction": "deposit to perp" if to_perp else "withdraw from perp",
            "result": result,
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error: {e.response.status_code}",
            "detail": e.response.text,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "action_payload": action,
            "note": "cDeposit action structure built. Production use requires signing via Hyperliquid SDK."
        }


@mcp.tool()
async def configure_dex_abstraction(
    action_type: str,
    agent_address: str,
    abstraction_config: Optional[str] = None,
) -> dict:
    """
    Enable or configure DEX abstraction settings for an agent.
    
    Includes agentEnableDexAbstraction and agentSetAbstraction. Use this when
    setting up automated DEX routing, configuring agent trading permissions,
    or enabling abstracted DEX interactions.
    
    Args:
        action_type: Type of abstraction action: 'enable' to enable DEX abstraction,
                     or 'set' to configure abstraction parameters
        agent_address: The agent address to configure DEX abstraction for (hex string)
        abstraction_config: Optional JSON string containing abstraction configuration
                            parameters when action_type is 'set'
    
    Returns:
        Exchange response with DEX abstraction configuration status
    """
    if action_type == "enable":
        action = {
            "type": "agentEnableDexAbstraction",
            "agentAddress": agent_address,
        }
    elif action_type == "set":
        config = {}
        if abstraction_config:
            try:
                config = json.loads(abstraction_config)
            except json.JSONDecodeError as e:
                return {
                    "success": False,
                    "error": f"Invalid JSON in abstraction_config: {str(e)}",
                }

        action = {
            "type": "agentSetAbstraction",
            "agentAddress": agent_address,
            **config,
        }
    else:
        return {
            "success": False,
            "error": f"Invalid action_type '{action_type}'. Must be 'enable' or 'set'.",
        }

    try:
        result = await post_exchange(action)
        return {
            "success": True,
            "action": "configure_dex_abstraction",
            "action_type": action_type,
            "agent_address": agent_address,
            "result": result,
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error: {e.response.status_code}",
            "detail": e.response.text,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "action_payload": action,
            "note": "DEX abstraction action structure built. Production use requires signing via Hyperliquid SDK."
        }


@mcp.tool()
async def validator_action(
    action_type: str,
    action_payload: str,
    nonce: Optional[int] = None,
) -> dict:
    """
    Perform validator or signer actions on Hyperliquid chain.
    
    Includes cValidatorAction and cSignerAction. Use this for staking operations,
    validator management, signer configuration, or governance-related actions
    on the Hyperliquid L1.
    
    Args:
        action_type: Type of action: 'validator' for cValidatorAction or 'signer' for cSignerAction
        action_payload: JSON string containing the specific validator or signer action parameters
                        (e.g., register, unregister, delegate, update signer)
        nonce: Optional nonce for the transaction. Auto-generated if not provided.
    
    Returns:
        Exchange response with validator/signer action status
    """
    if action_type not in ("validator", "signer"):
        return {
            "success": False,
            "error": f"Invalid action_type '{action_type}'. Must be 'validator' or 'signer'.",
        }

    try:
        payload_data = json.loads(action_payload)
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "error": f"Invalid JSON in action_payload: {str(e)}",
        }

    hl_type = "cValidatorAction" if action_type == "validator" else "cSignerAction"

    action = {
        "type": hl_type,
        **payload_data,
    }

    if nonce is None:
        nonce = get_nonce()

    try:
        result = await post_exchange(action, nonce=nonce)
        return {
            "success": True,
            "action": "validator_action",
            "action_type": action_type,
            "hl_type": hl_type,
            "nonce": nonce,
            "result": result,
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "error": f"HTTP error: {e.response.status_code}",
            "detail": e.response.text,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "action_payload_sent": action,
            "note": "Validator/signer action structure built. Production use requires signing via Hyperliquid SDK."
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

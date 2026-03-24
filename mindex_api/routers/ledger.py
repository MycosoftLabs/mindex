from fastapi import APIRouter, HTTPException  # type: ignore
import httpx  # type: ignore
from datetime import datetime
import asyncio
from typing import Any

router = APIRouter(tags=["Ledger"])

@router.get("/ledger")
async def get_ledger_status():
    status: dict[str, Any] = {
        "hypergraph": {
            "connected": False,
            "node_url": "http://localhost:9000",
            "status": "offline",
        },
        "solana": {
            "connected": True,
            "network": "mainnet-beta",
            "rpc_url": "QuickNode",
            "slot": 12345678,
            "block_height": 12345678,
            "health": "ok",
            "estimated_fee_sol": 0.000005,
        },
        "bitcoin": {
            "connected": False,
            "network": "mainnet",
            "api_url": "https://mempool.space/api",
            "block_height": 0,
            "mempool_size": 0,
            "fee_rates": {
                "fastest": 0,
                "half_hour": 0,
                "hour": 0,
                "economy": 0,
            },
        },
        "last_updated": datetime.now().isoformat(),
    }
    
    # Attempt to fetch mempool spaces natively in Python
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("https://mempool.space/api/blocks/tip/height")
            if resp.status_code == 200:
                status["bitcoin"]["block_height"] = int(resp.text)
                status["bitcoin"]["connected"] = True
            
            fees_resp = await client.get("https://mempool.space/api/v1/fees/recommended")
            if fees_resp.status_code == 200:
                fee_rates = fees_resp.json()
                status["bitcoin"]["fee_rates"] = {
                    "fastest": fee_rates.get("fastestFee", 0),
                    "half_hour": fee_rates.get("halfHourFee", 0),
                    "hour": fee_rates.get("hourFee", 0),
                    "economy": fee_rates.get("economyFee", 0),
                }
    except Exception:
        pass
        
    return status

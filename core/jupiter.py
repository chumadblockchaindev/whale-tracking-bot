import aiohttp
import base64
from solana.rpc.async_api import AsyncClient
from solders.transaction import VersionedTransaction

import config

client = AsyncClient(config.RPC_HTTP_URL)

JUPITER_QUOTE = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP = "https://quote-api.jup.ag/v6/swap"


async def get_quote(input_mint, output_mint, amount):
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": int(amount * 1e9),  # SOL → lamports
        "slippageBps": 1000,
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(JUPITER_QUOTE, params=params) as res:
            return await res.json()


async def build_swap_tx(quote, user_public_key: str):
    payload = {
        "quoteResponse": quote,
        "userPublicKey": user_public_key,
        "wrapAndUnwrapSol": True,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(JUPITER_SWAP, json=payload) as res:
            data = await res.json()
            return data.get("swapTransaction")


async def execute_jupiter_swap(quote, signer):
    swap_tx_b64 = await build_swap_tx(quote, signer.pubkey().to_base58())
    tx = VersionedTransaction.from_bytes(base64.b64decode(swap_tx_b64))
    signed_tx = signer.sign(tx)
    result = await client.send_raw_transaction(bytes(signed_tx))
    return result

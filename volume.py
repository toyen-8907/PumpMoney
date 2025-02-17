import asyncio
import json
import time
import websockets
from collections import defaultdict
import base58
import base64
import struct
import sys
import os
import ssl
from typing import Final
import hashlib
import httpx



from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.instruction import Instruction, AccountMeta
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction
from spl.token.instructions import get_associated_token_address
import spl.token.instructions as spl_token

from config import *
from construct import Struct, Int64ul, Flag

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE  # 不驗證 SSL 憑證

# 用於儲存不同代幣的交易數據
trade_data = defaultdict(lambda: {"1min": [], "5min": []})

async def connect_websocket():
    print("🚀 嘗試連線到 WebSocket...")
    return await websockets.connect(WSS_ENDPOINT, ssl=ssl_context)

     


def load_idl(path: str) -> dict:
    """
    Loads and returns the IDL from a JSON file.
    """
    with open(path, 'r') as f:
        return json.load(f)

#bonding_curve
EXPECTED_DISCRIMINATOR: Final[bytes] = struct.pack("<Q", 6966180631402821399)
LAMPORTS_PER_SOL: Final[int] = 1_000_000_000
TOKEN_DECIMALS: Final[int] = 6
CURVE_ADDRESS = "   "

class BondingCurveState:
    _STRUCT = Struct(
        "virtual_token_reserves" / Int64ul,
        "virtual_sol_reserves" / Int64ul,
        "real_token_reserves" / Int64ul,
        "real_sol_reserves" / Int64ul,
        "token_total_supply" / Int64ul,
        "complete" / Flag
    )

    def __init__(self, data: bytes) -> None:
        parsed = self._STRUCT.parse(data[8:])
        self.__dict__.update(parsed)

async def get_pump_curve_state(conn: AsyncClient, curve_address: Pubkey) -> BondingCurveState:
    try:
        response = await conn.get_account_info(curve_address)
        if not response.value or not response.value.data:
            print(f"Warning: No data found for bonding curve address {curve_address}")
            return None  # 這裡改成返回 None，而不是直接 raise error

        data = response.value.data
        if data[:8] != EXPECTED_DISCRIMINATOR:
            print(f"Warning: Invalid curve state discriminator for {curve_address}")
            return None

        return BondingCurveState(data)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:  # 如果是 429 Too Many Requests
            raise RuntimeError("⚠️ API 過載，請求次數超限")  # 直接拋出錯誤，讓 main_fun() 處理
        else:
            raise  # 其他錯誤直接拋出
    except httpx.RequestError as e:
        print(f"🚨 網絡錯誤: {e}, 可能是 API 連線問題")
        return None  # 避免因為網路問題影響流程
    
    
    
def calculate_pump_curve_price(curve_state: BondingCurveState) -> float:
    if curve_state.virtual_token_reserves <= 0 or curve_state.virtual_sol_reserves <= 0:
        raise ValueError("Invalid reserve state")

    return (curve_state.virtual_sol_reserves / LAMPORTS_PER_SOL) / (curve_state.virtual_token_reserves / 10 ** TOKEN_DECIMALS)



# global:buy discriminator - 16927863322537952870
# global:sell discriminator - 12502976635542562355
# global:create discriminator - 8576854823835016728
# account:BondingCurve discriminator - 6966180631402821399

# 解析tx 
def decode_transaction(tx_data, idl):
    decoded_instructions = []
    
    # Decode the base64 transaction data
    tx_data_decoded = base64.b64decode(tx_data['transaction'][0])
    
    # Check if it's a versioned transaction
    if tx_data.get('version') == 0:
        # Use solders library for versioned transactions
        transaction = VersionedTransaction.from_bytes(tx_data_decoded)
        instructions = transaction.message.instructions
        account_keys = transaction.message.account_keys
        print("Versioned transaction detected")
    else:
        # Use legacy deserialization for older transactions
        transaction = Transaction.deserialize(tx_data_decoded)
        instructions = transaction.instructions
        account_keys = transaction.message.account_keys
        print("Legacy transaction detected")
    
    print(f"Number of instructions: {len(instructions)}")
    
    for idx, ix in enumerate(instructions):
        program_id = str(account_keys[ix.program_id_index])
        print(f"\nInstruction {idx}:")
        print(f"Program ID: {program_id}")
        print(f"IDL program address: {idl['metadata']['address']}")
        
        if program_id == idl['metadata']['address']:
            ix_data = bytes(ix.data)
            discriminator = struct.unpack('<Q', ix_data[:8])[0]
            
            print(f"Discriminator: {discriminator:016x}")
            
            for idl_ix in idl['instructions']:
                idl_discriminator = calculate_discriminator(f"global:{idl_ix['name']}")
                print(f"Checking against IDL instruction: {idl_ix['name']} with discriminator {idl_discriminator:016x}")
                
                if discriminator == idl_discriminator:
                    decoded_args = decode_instruction(ix_data, idl_ix)
                    accounts = [str(account_keys[acc_idx]) for acc_idx in ix.accounts]
                    decoded_instructions.append({
                        'name': idl_ix['name'],
                        'args': decoded_args,
                        'accounts': accounts,
                        'program': program_id
                    })
                    break
            else:
                decoded_instructions.append({
                    'name': 'Unknown',
                    'data': ix_data.hex(),
                    'accounts': [str(account_keys[acc_idx]) for acc_idx in ix.accounts],
                    'program': program_id
                })
        else:
            instruction_name = 'External'
            if program_id == 'ComputeBudget111111111111111111111111111111':
                if ix.data[:1] == b'\x03':
                    instruction_name = 'ComputeBudget: Set compute unit limit'
                elif ix.data[:1] == b'\x02':
                    instruction_name = 'ComputeBudget: Set compute unit price'
            elif program_id == 'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL':
                instruction_name = 'Associated Token Account: Create'
            
            decoded_instructions.append({
                'name': instruction_name,
                'programId': program_id,
                'data': bytes(ix.data).hex(),
                'accounts': [str(account_keys[acc_idx]) for acc_idx in ix.accounts]
            })

    return decoded_instructions





async def process_block(block_data):
    """Decode each transaction in the block and record buy/sell events."""
    block_time = int(time.time())
    transactions = block_data.get("transactions", [])

    for tx in transactions:
        try:
            tx_info = decode_transaction(tx, load_idl("pump_fun_idl.json"))

            # Check for 'buy' or 'sell' instructions
            for instr in tx_info:
                if instr['name'] in ['buy', 'sell']:
                    token = instr['args']['mint']
                    amount = instr['args']['amount']
                    trade_type = 'buy' if instr['name'] == 'buy' else 'sell'

                    # 儲存交易數據
                    trade_data[token]["1min"].append((block_time, trade_type, amount))
                    trade_data[token]["5min"].append((block_time, trade_type, amount))

                    print(f"Token: {token} | {trade_type.upper()} | Amount: {amount}")

            cleanup_old_data()
            save_data()

        except Exception as e:
            print(f"Error processing transaction: {e}")




async def listen_blocks(websocket):
    """Subscribe to new Solana blocks via WebSocket and process them."""
    
    try:
        idl = load_idl('pump_fun_idl.json')
        buy_discriminator = 16927863322537952870
        sell_discrimnator = 12502976635542562355
        print(PUMP_PROGRAM)
        # 訂閱區塊
        await websocket.send(json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "blockSubscribe",
            "params": [
               {"mentionsAccountOrProgram": str(PUMP_PROGRAM)},
            {
                "commitment": "confirmed",
                "encoding": "base64",
                "showRewards": False,
                "transactionDetails": "full",
                "maxSupportedTransactionVersion": 0
            }
        ]
            
        }))
        
        print(f"Subscribed to blocks mentioning program: {PUMP_PROGRAM}")
        
        
        ping_interval = 20
        last_ping_time = time.time()

        while True:
            try:
                current_time = time.time()
                if current_time - last_ping_time > ping_interval:
                    await websocket.ping()
                    last_ping_time = current_time

                response = await asyncio.wait_for(websocket.recv(), timeout=30)
                data = json.loads(response)

                if "params" in data and "result" in data["params"]:
                    block_data = data["params"]["result"]
                    await process_block(block_data)
                    
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:  # 如果是 429 Too Many Requests
                    raise RuntimeError("⚠️ API 過載，請求次數超限")
            except asyncio.TimeoutError:
                print("No data received for 30 seconds, sending ping...")
                await websocket.ping()
                last_ping_time = time.time()
            except websockets.exceptions.ConnectionClosed:
                print("WebSocket connection closed. Reconnecting...")
                raise            
    except:
        print("反正是出錯了")


def cleanup_old_data():
    """移除超過 1 分鐘和 5 分鐘的交易數據"""
    current_time = int(time.time())

    for token in list(trade_data.keys()):
        trade_data[token]["1min"] = [
            (t, tt, amt)
            for t, tt, amt in trade_data[token]["1min"]
            if current_time - t <= 60
        ]
        trade_data[token]["5min"] = [
            (t, tt, amt)
            for t, tt, amt in trade_data[token]["5min"]
            if current_time - t <= 300
        ]

def save_data():
    """將數據存入 JSON"""
    with open("trade_volume.json", "w") as f:
        json.dump(trade_data, f, indent=4)

async def main_for_volume():
    try:
        print("✅ WebSocket 連線中.............")
        websocket = await connect_websocket(max_size=2**20, compression=None)
        print("✅ WebSocket 連線成功")
        while True:
                try:
                    print("🤖 等待新代幣交易紀錄...")
                    token_data = await listen_for_create_transaction_blocksubscribe(websocket)
                    print("新代幣交易💰: --------------------------------------")



if __name__ == "__main__":
    asyncio.run(main_for_volume())

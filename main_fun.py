import asyncio
import json
import websockets
import base58
import base64
import struct
import sys
import os
import ssl
import websocket
from typing import Final
import time  # <-- Make sure we import time if we're using it
import httpx


# 使用websocket-client套件的寫法

import hashlib


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

LAMPORTS_PER_SOL: Final[int] = 1_000_000_000
TOKEN_DECIMALS: Final[int] = 6
CURVE_ADDRESS = "   "

api_counter = 0

# ------------------------
# 1. Define or import your TokenStorage class
# ------------------------
from dataclasses import dataclass, field
from typing import Dict, Any

@dataclass
class TokenStorage:
    tokenCA: str
    bonding_curve_address: str
    symbol: str
    _data: Dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    
    def __post_init__(self):
        """初始化時，把值存入內部字典"""
        self._data['tokenCA'] = self.tokenCA
        self._data['bonding_curve_address'] = self.bonding_curve_address
        self._data['symbol'] = self.symbol
    
    def __getitem__(self, key: str) -> Any:
        """允許使用 obj['tokenCA'] 這種方式獲取值"""
        if key not in self._data:
            raise KeyError(f"Key '{key}' is not allowed. Allowed keys: {list(self._data.keys())}")
        return self._data[key]
    
    def __setitem__(self, key: str, value: Any):
        """允許使用 obj['tokenCA'] = 'new_value' 修改值"""
        if key not in self._data:
            raise KeyError(f"Key '{key}' is not allowed. Allowed keys: {list(self._data.keys())}")
        self._data[key] = value
    
    def __repr__(self):
        """返回物件的可讀性表示"""
        return f"TokenStorage(tokenCA={self._data['tokenCA']}, bonding_curve_address={self._data['bonding_curve_address']}, symbol={self._data['symbol']})"

# ------------------------
# 2. Define your function to load the IDL (to fix the NameError)
# ------------------------
def load_idl(path: str) -> dict:
    """
    Loads and returns the IDL from a JSON file.
    """
    with open(path, 'r') as f:
        return json.load(f)

# 加載 .env 檔案中的變數



# ------------------------
# 3. Bonding Curve / Price Logic
# ------------------------
EXPECTED_DISCRIMINATOR: Final[bytes] = struct.pack("<Q", 6966180631402821399)

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




# Load the IDL JSON file
with open('pump_fun_idl.json', 'r') as f:
    idl = json.load(f)


# ------------------------
# 4. Handle your "create" instruction decoding
#    We can unify "parse_create_instruction()" and "decode_create_instruction()" 
#    or just call parse_create_instruction inside decode_create_instruction
# ------------------------

def decode_create_instruction(ix_data, ix_def, accounts):
    args = {}
    offset = 8  # Skip 8-byte discriminator

    for arg in ix_def['args']:
        if arg['type'] == 'string':
            length = struct.unpack_from('<I', ix_data, offset)[0]
            offset += 4
            value = ix_data[offset:offset+length].decode('utf-8')
            offset += length
        elif arg['type'] == 'publicKey':
            value = base64.b64encode(ix_data[offset:offset+32]).decode('utf-8')
            offset += 32
        else:
            raise ValueError(f"Unsupported type: {arg['type']}")
        
        args[arg['name']] = value

    # Add accounts
    args['mint'] = str(accounts[0])
    args['bondingCurve'] = str(accounts[2])
    args['associatedBondingCurve'] = str(accounts[3])
    args['user'] = str(accounts[7])

    return args




# Extract the "create" instruction definition
create_instruction = next(instr for instr in idl['instructions'] if instr['name'] == 'create')

def parse_create_instruction(data):
    if len(data) < 8:
        return None
    offset = 8
    parsed_data = {}

    # Parse fields based on CreateEvent structure
    fields = [
        ('name', 'string'),
        ('symbol', 'string'),
        ('uri', 'string'),
        ('mint', 'publicKey'),
        ('bondingCurve', 'publicKey'),
        ('user', 'publicKey'),
    ]

    try:
        for field_name, field_type in fields:
            if field_type == 'string':
                length = struct.unpack('<I', data[offset:offset+4])[0]
                offset += 4
                value = data[offset:offset+length].decode('utf-8')
                offset += length
            elif field_type == 'publicKey':
                value = base58.b58encode(data[offset:offset+32]).decode('utf-8')
                offset += 32

            parsed_data[field_name] = value

        return parsed_data
    except:
        return None

def print_transaction_details(log_data):
    print(f"Signature: {log_data.get('signature')}")
    
    for log in log_data.get('logs', []):
        if log.startswith("Program data:"):
            try:
                data = base58.b58decode(log.split(": ")[1]).decode('utf-8')
                print(f"Data: {data}")
            except:
                pass
            
# ------------------------
# 5. The two main "listeners" for block notifications
# ------------------------            
async def connect_websocket():
    return await websockets.connect(WSS_ENDPOINT, ssl=ssl_context)

async def listen_for_create_transaction_blocksubscribe(websocket):
    idl = load_idl('pump_fun_idl.json')
    create_discriminator = 8576854823835016728
    # print(f"decode_create_instruction in globals: {'decode_create_instruction' in globals()}")

    subscription_message = json.dumps({
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
    })
    await websocket.send(subscription_message)
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
            
            if 'method' in data and data['method'] == 'blockNotification':
                if 'params' in data and 'result' in data['params']:
                    block_data = data['params']['result']
                    if 'value' in block_data and 'block' in block_data['value']:
                        block = block_data['value']['block']
                        if 'transactions' in block:
                            for tx in block['transactions']:
                                if isinstance(tx, dict) and 'transaction' in tx:
                                    tx_data_decoded = base64.b64decode(tx['transaction'][0])
                                    transaction = VersionedTransaction.from_bytes(tx_data_decoded)
                                    
                                    for ix in transaction.message.instructions:
                                        if str(transaction.message.account_keys[ix.program_id_index]) == str(PUMP_PROGRAM):
                                            ix_data = bytes(ix.data)
                                            discriminator = struct.unpack('<Q', ix_data[:8])[0]
                                            
                                            if discriminator == create_discriminator:
                                                create_ix = next(instr for instr in idl['instructions'] if instr['name'] == 'create')
                                                account_keys = [
                                                    str(transaction.message.account_keys[index]) 
                                                    for index in ix.accounts if index < len(transaction.message.account_keys)
                                                ]
                                                decoded_args = decode_create_instruction(ix_data, create_ix, account_keys)
                                                return decoded_args
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
            
            
            
async def listen_for_create_transaction(websocket):
    idl = load_idl('idl/pump_fun_idl.json')
    create_discriminator = 8576854823835016728
    
    subscription_message = json.dumps({
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
    })
    await websocket.send(subscription_message)
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
            
            if 'method' in data and data['method'] == 'blockNotification':
                if 'params' in data and 'result' in data['params']:
                    block_data = data['params']['result']
                    if 'value' in block_data and 'block' in block_data['value']:
                        block = block_data['value']['block']
                        if 'transactions' in block:
                            for tx in block['transactions']:
                                if isinstance(tx, dict) and 'transaction' in tx:
                                    tx_data_decoded = base64.b64decode(tx['transaction'][0])
                                    transaction = VersionedTransaction.from_bytes(tx_data_decoded)
                                    
                                    for ix in transaction.message.instructions:
                                        if str(transaction.message.account_keys[ix.program_id_index]) == str(PUMP_PROGRAM):
                                            ix_data = bytes(ix.data)
                                            discriminator = struct.unpack('<Q', ix_data[:8])[0]
                                            
                                            if discriminator == create_discriminator:
                                                create_ix = next(instr for instr in idl['instructions'] if instr['name'] == 'create')
                                                account_keys = [str(transaction.message.account_keys[index]) for index in ix.accounts]
                                                decoded_args = decode_create_instruction(ix_data, create_ix, account_keys)
                                                return decoded_args
        except asyncio.TimeoutError:
            print("No data received for 30 seconds, sending ping...")
            await websocket.ping()
            last_ping_time = time.time()
        except websockets.exceptions.ConnectionClosed:
            print("WebSocket connection closed. Reconnecting...")
            raise
        
        
# ------------------------
# 6. Main entry
# ------------------------        
        
        
async def main_fun():
    websocket = await connect_websocket()  # 直接 await 連線
    global api_counter
    try:
        while True:
            print("🤖 🤖 🤖 等待新代幣創建...")
            try:
                token_data = await listen_for_create_transaction_blocksubscribe(websocket)
            except RuntimeError as e:
                print(f"🚨 {e}，暫停 1 秒後繼續...")
                await asyncio.sleep(1)
            print("新代幣💰 💰 💰: -----------------------------------------------------------------")
            print(json.dumps(token_data, indent=2))

            mint = Pubkey.from_string(token_data['mint'])
            bonding_curve = Pubkey.from_string(token_data['bondingCurve'])
            associated_bonding_curve = Pubkey.from_string(token_data['associatedBondingCurve'])
            api_counter += 5.1
            if api_counter >=5:
                await asyncio.sleep(1)
                api_counter = 0
            # 獲取代幣價格
            async with AsyncClient(RPC_ENDPOINT_2) as client:
                try:
                    curve_state = await get_pump_curve_state(client, bonding_curve)
                    if curve_state is None:
                        print(f"代幣 {token_data['symbol']} 尚未有人購買")
                        continue
                    token_price_sol = calculate_pump_curve_price(curve_state)
                    print(f"Bonding curve address: {bonding_curve}")
                    print(f"💵 代幣價格: {token_price_sol:.10f} SOL")
                except RuntimeError as e:
                    print(f"🚨 {e}，暫停 1 秒後繼續...")
                    await asyncio.sleep(1)
            
    except websockets.exceptions.ConnectionClosed:
        print("WebSocket connection closed. Reconnecting...")
        await main_fun()  # 當 WebSocket 斷開時，重新執行 main_fun()
    finally:
        await websocket.close()  # 確保 WebSocket 連線被關閉


if __name__ == "__main__":
    asyncio.run(main_fun())

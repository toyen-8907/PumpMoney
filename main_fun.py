import asyncio
import json
import websockets
import base58
import base64
import struct
import sys
import os
import ssl
from typing import Final
from config import WSS_ENDPOINT, PUMP_PROGRAM, RPC_ENDPOINT
from construct import Struct, Int64ul, Flag
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey

# SSL 設定
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE  # 不驗證 SSL 憑證

# 記錄已經處理過的交易，避免重複輸出
seen_signatures = set()
already_subscribed = False  # 避免多次訂閱

# 加載 .env 檔案中的變數
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 加載 IDL JSON 文件
with open('pump_fun_idl.json', 'r') as f:
    idl = json.load(f)

# Bonding Curve 相關參數
LAMPORTS_PER_SOL: Final[int] = 1_000_000_000
TOKEN_DECIMALS: Final[int] = 6
CURVE_ADDRESS = ""

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

async def get_bonding_curve_state(conn: AsyncClient, curve_address: Pubkey) -> BondingCurveState:
    response = await conn.get_account_info(curve_address)
    if not response.value or not response.value.data:
        raise ValueError("Invalid curve state: No data")

    data = response.value.data
    if data[:8] != EXPECTED_DISCRIMINATOR:
        raise ValueError("Invalid curve state discriminator")

    return BondingCurveState(data)

def calculate_bonding_curve_price(curve_state: BondingCurveState) -> float:
    if curve_state.virtual_token_reserves <= 0 or curve_state.virtual_sol_reserves <= 0:
        raise ValueError("Invalid reserve state")

    return (curve_state.virtual_sol_reserves / LAMPORTS_PER_SOL) / (curve_state.virtual_token_reserves / 10 ** TOKEN_DECIMALS)

# 提取 "create" 指令定義
create_instruction = next(instr for instr in idl['instructions'] if instr['name'] == 'create')

def parse_create_instruction(data):
    if len(data) < 8:
        return None
    offset = 8
    parsed_data = {}

    # 解析 CreateEvent 結構
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

def process_logs(log_data, logs):
    """
    解析交易日誌，避免重複輸出相同的交易數據
    """
    signature = log_data.get('signature')

    if signature in seen_signatures:
        return  # 如果已處理過此交易，則跳過

    seen_signatures.add(signature)

    for log in logs:
        if "Program data:" in log:
            try:
                encoded_data = log.split(": ")[1]
                decoded_data = base64.b64decode(encoded_data)
                parsed_data = parse_create_instruction(decoded_data)
                
                if parsed_data and 'name' in parsed_data:
                    print("Signature:", signature)
                    for key, value in parsed_data.items():
                        print(f"{key}: {value}")
                        
                        if key == "bondingCurve":
                            curve_address = value.strip()
                            print(f"Extracted bondingCurve address: '{curve_address}'")
                            if len(curve_address) == 44:
                                asyncio.create_task(fetch_and_print_token_price(curve_address))

            except Exception as e:
                print(f"Failed to decode: {log}, Error: {e}")

async def fetch_and_print_token_price(curve_address):
    try:
        async with AsyncClient(RPC_ENDPOINT) as conn:
            bonding_curve_state = await get_bonding_curve_state(conn, Pubkey.from_string(curve_address))
            token_price_sol = calculate_bonding_curve_price(bonding_curve_state)
            print(f"Token price for {curve_address}: {token_price_sol:.10f} SOL")
    except Exception as e:
        print(f"Error fetching bonding curve price: {e}")

async def listen_for_new_tokens():
    global already_subscribed
    while True:
        websocket = None
        try:
            websocket = await websockets.connect(WSS_ENDPOINT, ssl=ssl_context)
            print("Connected to WebSocket, listening for new token creations...")

            if not already_subscribed:
                subscription_message = json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": ["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"]},
                        {"commitment": "processed"}
                    ]
                })
                await websocket.send(subscription_message)
                already_subscribed = True
                print("Subscription sent.")

            while True:
                response = await websocket.recv()
                data = json.loads(response)

                if 'method' in data and data['method'] == 'logsNotification':
                    log_data = data['params']['result']['value']
                    logs = log_data.get('logs', [])

                    if any("Program log: Instruction: Create" in log for log in logs):
                        process_logs(log_data, logs)

        except websockets.exceptions.ConnectionClosedError as e:
            print(f"WebSocket connection closed unexpectedly: {e}")
        except Exception as e:
            print(f"Unexpected error: {e}")
        finally:
            if websocket:
                await websocket.close()
                print("WebSocket connection closed. Reconnecting in 5 seconds...")
            already_subscribed = False  # 只有完全斷線後才允許重新訂閱
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(listen_for_new_tokens())

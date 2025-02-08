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
import requests
from typing import Final
from config import WSS_ENDPOINT, PUMP_PROGRAM
from construct import Struct, Int64ul, Flag
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from config import RPC_ENDPOINT

# SSL 設定
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE  # 不驗證 SSL 憑證

LAMPORTS_PER_SOL: Final[int] = 1_000_000_000
TOKEN_DECIMALS: Final[int] = 6

# Bonding Curve 解析
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

async def get_bonding_curve_state(conn: AsyncClient, curve_address: Pubkey):
    """
    取得 Bonding Curve 狀態
    """
    print(f"Fetching bonding curve state for: {curve_address}")

    response = await conn.get_account_info(curve_address)
    print(f"Raw response: {response}")

    if not response.value or not response.value.data:
        print(f"Warning: Bonding Curve {curve_address} has no data yet. Sleeping for 5 seconds...")
        await asyncio.sleep(5)  # 如果沒有數據，等待 5 秒
        return None

    data = response.value.data
    if data[:8] != EXPECTED_DISCRIMINATOR:
        print(f"Error: Invalid curve state discriminator for {curve_address}")
        return None

    return BondingCurveState(data)

def calculate_bonding_curve_price(curve_state: BondingCurveState) -> float:
    """
    計算代幣價格
    """
    if curve_state.virtual_token_reserves <= 0 or curve_state.virtual_sol_reserves <= 0:
        return 0.0

    return (curve_state.virtual_sol_reserves / LAMPORTS_PER_SOL) / (curve_state.virtual_token_reserves / 10 ** TOKEN_DECIMALS)

async def process_bonding_curve(curve_address):
    """
    異步處理 Bonding Curve 查詢
    """
    async with AsyncClient(RPC_ENDPOINT) as conn:
        try:
            curve_pubkey = Pubkey.from_string(curve_address)
            bonding_curve_state = await get_bonding_curve_state(conn, curve_pubkey)

            if bonding_curve_state:
                token_price_sol = calculate_bonding_curve_price(bonding_curve_state)
                print(f"Token price for {curve_address}: {token_price_sol:.10f} SOL")
            else:
                print(f"Skipping {curve_address} due to missing data.")
        except Exception as e:
            print(f"Error processing {curve_address}: {e}")

def parse_create_instruction(data):
    """
    解析 Create 指令
    """
    if len(data) < 8:
        return None
    offset = 8
    parsed_data = {}

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

async def listen_for_new_tokens():
    """
    監聽新代幣的創建
    """
    while True:
        try:
            async with websockets.connect(WSS_ENDPOINT, ssl=ssl_context) as websocket:
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
                print("Listening for new token creations...")

                response = await websocket.recv()
                print(f"Subscription response: {response}")

                while True:
                    try:
                        response = await websocket.recv()
                        data = json.loads(response)

                        if 'method' in data and data['method'] == 'logsNotification':
                            log_data = data['params']['result']['value']
                            logs = log_data.get('logs', [])
                            
                            if any("Program log: Instruction: Create" in log for log in logs):
                                for log in logs:
                                    if "Program data:" in log:
                                        try:
                                            encoded_data = log.split(": ")[1]
                                            decoded_data = base64.b64decode(encoded_data)

                                            parsed_data = parse_create_instruction(decoded_data)
                                            if parsed_data and 'name' in parsed_data:
                                                print("Signature:", log_data.get('signature'))
                                                for key, value in parsed_data.items():
                                                    print(f"{key}: {value}")

                                                    if key == "bondingCurve":
                                                        curve_address = value.strip()
                                                        print(f"Extracted bondingCurve address: '{curve_address}'")
                                                        print(f"Length: {len(curve_address)}")

                                                        if len(curve_address) == 44:
                                                            asyncio.create_task(process_bonding_curve(curve_address))
                                                        else:
                                                            print(f"Invalid bondingCurve address: {curve_address}")
                                        except Exception as e:
                                            print(f"Failed to decode: {log}")
                                            print(f"Error: {str(e)}")
                    except Exception as e:
                        print(f"An error occurred while processing message: {e}")
                        break
        except Exception as e:
            print(f"Connection error: {e}")
            print("Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(listen_for_new_tokens())

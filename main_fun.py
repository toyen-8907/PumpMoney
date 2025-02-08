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
from config import WSS_ENDPOINT, PUMP_PROGRAM
from construct import Struct, Int64ul, Flag
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from config import RPC_ENDPOINT
# 使用websocket-client套件的寫法

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE  # 不驗證 SSL 憑證

ws = websocket.create_connection(
    WSS_ENDPOINT,
    sslopt={"cert_reqs": ssl.CERT_NONE}  # 禁用證書驗證
)

LAMPORTS_PER_SOL: Final[int] = 1_000_000_000
TOKEN_DECIMALS: Final[int] = 6
CURVE_ADDRESS = "   "


# 加載 .env 檔案中的變數


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Here and later all the discriminators are precalculated. See learning-examples/discriminator.py
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




# Load the IDL JSON file
with open('pump_fun_idl.json', 'r') as f:
    idl = json.load(f)

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

async def listen_for_new_tokens():
    while True:
        try:
            async with websockets.connect(WSS_ENDPOINT, ssl=ssl_context) as websocket:
                subscription_message = json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [str("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")]},
                        {"commitment": "processed"}
                    ]
                })
                await websocket.send(subscription_message)
                print(f"Listening for new token creations from program: 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")

                # Wait for subscription confirmation
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
                                                    #計算代幣價格
                                                    if key == "bondingCurve":    
                                                                                                  
                                                        CURVE_ADDRESS = value
                                                        print(f"Extracted bondingCurve address: '{CURVE_ADDRESS}'")  # 加上單引號看是否有多餘空格
                                                        print(f"Length: {len(CURVE_ADDRESS)}")

                                                        if CURVE_ADDRESS:
                                                            try:
                                                                async with AsyncClient(RPC_ENDPOINT) as conn:
                                                                    curve_address = Pubkey.from_string(CURVE_ADDRESS)
                                                                    bonding_curve_state = await get_bonding_curve_state(conn, curve_address)
                                                                    token_price_sol = calculate_bonding_curve_price(bonding_curve_state)

                                                                    print("Token price:")
                                                                    print(f"  {token_price_sol:.10f} SOL")
                                                            except ValueError as e:
                                                                print(f"Error: {e}")
                                                print("##########################################################################################")

                                                    
                                               
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
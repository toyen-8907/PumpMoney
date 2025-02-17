import asyncio
import base64
import json
import struct
import time

from solders.transaction import VersionedTransaction  # 依你實際使用的套件而定
# 可能也要 import httpx, websockets, etc.

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
BUY_DISCRIMINATOR = 16927863322537952870

async def listen_for_create_transaction_blocksubscribe(websocket):
    idl = load_idl('pump_fun_idl.json')  # 你的 IDL
    
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
                                    
                                    # --- 尋找外部指令中，是否有呼叫 PUMP_PROGRAM 的指令 ---
                                    for ix in transaction.message.instructions:
                                        program_id = transaction.message.account_keys[ix.program_id_index]
                                        if str(program_id) == PUMP_PROGRAM:
                                            ix_data = bytes(ix.data)
                                            discriminator = struct.unpack('<Q', ix_data[:8])[0]
                                            
                                            if discriminator == BUY_DISCRIMINATOR:
                                                # 這裡表示「確認是 buy 指令」
                                                print("=== Detected BUY instruction ===")
                                                
                                                # 1) 從 ix_data 中 decode 出來的參數（如果 IDL 裡面有定義，比如 price, amount 等）
                                                #    這裡假設 decode_create_instruction 是你寫來解析 IDL 參數的函式
                                                buy_ix_def = next(instr for instr in idl['instructions'] if instr['name'] == 'buy')
                                                account_keys = [
                                                    str(transaction.message.account_keys[a_idx]) 
                                                    for a_idx in ix.accounts if a_idx < len(transaction.message.account_keys)
                                                ]
                                                decoded_args = decode_create_instruction(ix_data, buy_ix_def, account_keys)
                                                
                                                # 根據 IDL 你可能可以拿到具體的 price 或 amount
                                                # 例如:
                                                # buy_price = decoded_args["price"]  # 看你 IDL 內部定義
                                                # buy_amount = decoded_args["amount"]
                                                
                                                # 2) 在這筆交易的 meta/innerInstructions 中，找出對應的 token transfer、system transfer
                                                #    以取得實際的花費 SOL、買到多少 token
                                                #    注意：要先從 postTokenBalances & preTokenBalances 或 innerInstructions 的 transfer type 裡面對應
                                                
                                                # 先示範從 innerInstructions 尋找
                                                # 由於你不一定知道 index 幾，最保險就是全部搜尋:
                                                sol_spent = 0
                                                token_mint = None
                                                token_amount = 0
                                                
                                                # 下面的 meta 可能要從 websocket 回傳的 block 裡頭去找
                                                # 但是在 solana-py / solders 裡面有時候會直接把 meta 包進 transaction 中
                                                # 如果沒有，可能要從 tx["meta"] 取 (需確定 data['params']['result'] 裡面有包含 meta)
                                                # 這裡假設 meta 在同一層可以取：
                                                if 'meta' in tx:
                                                    meta = tx['meta']
                                                    # a. 找 System Program transfer
                                                    # b. 找 SPL Token transfer
                                                    if 'innerInstructions' in meta:
                                                        for inner_ix in meta['innerInstructions']:
                                                            # inner_ix["index"]  # 對應外部指令 index
                                                            for inst in inner_ix['instructions']:
                                                                # system transfer
                                                                if inst.get('programId') == "11111111111111111111111111111111":
                                                                    parsed = inst.get('parsed')
                                                                    if parsed and parsed.get('type') == 'transfer':
                                                                        info = parsed.get('info', {})
                                                                        lamports = int(info.get('lamports', 0))
                                                                        source = info.get('source')
                                                                        # 你可以加一些判斷 source 是否是買家帳戶 ...
                                                                        sol_spent += lamports
                                                                
                                                                # SPL token transfer
                                                                if inst.get('programId') == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
                                                                    parsed = inst.get('parsed')
                                                                    if parsed and parsed.get('type') == 'transfer':
                                                                        info = parsed.get('info', {})
                                                                        amount_str = info.get('amount', "0")
                                                                        authority = info.get('authority')
                                                                        destination = info.get('destination')
                                                                        source = info.get('source')
                                                                        # 同樣可以判斷這個 transfer 是不是跟買家有關
                                                                        # amount 可能很大，要轉成 float(以 decimals 為基準)
                                                                        token_amount = int(amount_str)
                                                
                                                # lamports to SOL
                                                sol_spent_in_sol = sol_spent / 1_000_000_000
                                                
                                                # 假設再從 postTokenBalances - preTokenBalances 去看 token 差值
                                                # token_mint = "HfJVjBdkhAD2ynVM8PdTSii4ECZdsxNTCx5wpEqUpump"  # 亦可從指令 accounts 找到
                                                
                                                # 最後就可以返回或印出結果
                                                result = {
                                                    "buy_price": decoded_args.get("price", None),  # or 你自行計算 ratio
                                                    "buy_token_amount": token_amount,
                                                    "sol_spent": sol_spent_in_sol,
                                                    "token_mint": token_mint
                                                }
                                                print("Buy Info:", result)
                                                
                                                return result
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise RuntimeError("⚠️ API 過載，請求次數超限")
        except asyncio.TimeoutError:
            print("No data received for 30 seconds, sending ping...")
            await websocket.ping()
            last_ping_time = time.time()
        except websockets.exceptions.ConnectionClosed:
            print("WebSocket connection closed. Reconnecting...")
            raise

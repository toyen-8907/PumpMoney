
async def listen_for_create_transaction_blocksubscribe(websocket):
    idl = load_idl('pump_fun_idl.json')
    buy_discriminator = 16927863322537952870
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
                                            
                                            if discriminator == buy_discriminator:
                                                buy_ix = next(instr for instr in idl['instructions'] if instr['name'] == 'buy')
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

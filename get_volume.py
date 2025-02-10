import requests
import os
from dotenv import load_dotenv

load_dotenv()

# CallStatic API 設定
API_URL = "https://api.callstaticrpc.com/pumpfun/v1/token/volume"
CALLSTATIC_BEARER_TOKEN = os.getenv("CALLSTATIC_BEARER_TOKEN")  # 從 .env 環境變數讀取 API Key
TOKEN_MINT = "GGVEn1FhMMvWg1BeEpUkKqiasDCoTqMajRy99ZMfpump"  # 代幣 Mint 地址

def get_token_volume():
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {CALLSTATIC_BEARER_TOKEN}"
    }
    
    params = {"token": TOKEN_MINT}
    
    response = requests.get(API_URL, headers=headers, params=params)
    
    if response.status_code == 200:
        data = response.json()
        print("✅ API 回應成功:")
        print(data)
        
        if data.get("success") and "data" in data and len(data["data"]) > 0:
            token_data = data["data"][0]
            buy_volume_24h = token_data.get("buy_volume_24h", "N/A")
            sell_volume_24h = token_data.get("sell_volume_24h", "N/A")
            total_volume_24h = int(buy_volume_24h) + int(sell_volume_24h)
            
            buy_volume_7d = token_data.get("buy_volume_1w", "N/A")
            sell_volume_7d = token_data.get("sell_volume_1w", "N/A")
            total_volume_7d = int(buy_volume_7d) + int(sell_volume_7d)
            
            print(f"✅ 24 小時買入量: {buy_volume_24h} USD")
            print(f"✅ 24 小時賣出量: {sell_volume_24h} USD")
            print(f"✅ 24 小時總交易量: {total_volume_24h} USD")
            print(f"✅ 7 天總交易量: {total_volume_7d} USD")
        else:
            print("❌ 無法解析 API 回應數據，請檢查 Mint 地址是否正確。")
    elif response.status_code == 404:
        print("❌ 找不到該代幣的交易量數據，請檢查 Mint 地址是否正確。")
    else:
        print(f"❌ API 請求失敗: {response.status_code}")
        print(response.text)

if __name__ == "__main__":
    get_token_volume()

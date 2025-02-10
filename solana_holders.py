import requests
import os
from dotenv import load_dotenv

# 載入 .env 檔案
load_dotenv()

# CallStatic API 設定
API_URL = "https://api.callstaticrpc.com/pumpfun/v1/token/marketData"
CALLSTATIC_BEARER_TOKEN = os.getenv("CALLSTATIC_BEARER_TOKEN")
TOKEN_MINT = "GGVEn1FhMMvWg1BeEpUkKqiasDCoTqMajRy99ZMfpump"  # 代幣 Mint 地址

# 設定請求標頭
headers = {
    "Accept": "application/json",
    "Authorization": f"Bearer {CALLSTATIC_BEARER_TOKEN}"
}

# 設定 Query 參數
params = {"token": TOKEN_MINT}

# 發送 GET API 請求
response = requests.get(API_URL, headers=headers, params=params)

# 解析 API 回應
if response.status_code == 200:
    response_json = response.json()
    print("✅ API 回應成功:")
    print(response_json)

    # 取得 data 部分
    if response_json.get("success") and "data" in response_json:
        data = response_json["data"]

        # 提取市值資訊
        market_cap = data.get("current_market_cap")
        price_sol = data.get("price_sol")
        price_usd = data.get("price_usd")
        bonding_market_cap = data.get("bonding_market_cap")
        bonding_progress = data.get("bonding_progress")

        print(f"✅ 代幣市值 (Market Cap): {market_cap} USD")
        print(f"✅ 代幣價格 (SOL): {price_sol} SOL")
        print(f"✅ 代幣價格 (USD): {price_usd} USD")
        print(f"✅ Bonding 市值: {bonding_market_cap} USD")
        print(f"✅ Bonding 進度: {bonding_progress}%")
    else:
        print("❌ API 回應格式不符合預期，可能是 `mint` 地址錯誤。")

elif response.status_code == 404:
    print("❌ 找不到該代幣的市場數據，請檢查 Mint 地址是否正確。")
else:
    print(f"❌ API 請求失敗: {response.status_code}")
    print(response.text)

import requests
import os
from dotenv import load_dotenv

load_dotenv()

# CallStatic API 設定
API_URL = "https://api.callstaticrpc.com/pumpfun/v1/historical/trades/byToken"
CALLSTATIC_BEARER_TOKEN = os.getenv("CALLSTATIC_BEARER_TOKEN")  # 從 .env 讀取 API Key
TOKEN_MINT = "EnbmFmgxfDfWUj389HN8pt5wjXBTkxk9vL3dwiD3pump"  # 代幣 Mint 地址

def get_holders_from_trades():
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {CALLSTATIC_BEARER_TOKEN}"
    }

    unique_holders = set()
    cursor = None  # 初始 cursor 為 None，API 會自動返回第一批數據
    total_trades = 0
    max_iterations = 20  # 增加最大迴圈次數，提高準確度
    iteration = 0

    while iteration < max_iterations:
        params = {"token": TOKEN_MINT, "limit": 1000}  # 設置較大 limit 獲取更多數據
        if cursor:
            params["cursor"] = cursor  # 若有 cursor，則查詢下一頁數據

        response = requests.get(API_URL, headers=headers, params=params)

        if response.status_code == 200:
            data = response.json()
            print("✅ API 回應成功：")

            if data.get("success") and "data" in data:
                trades = data["data"]
                total_trades += len(trades)

                # 提取交易數據中的持有者地址（包括買家和賣家）
                for trade in trades:
                    buyer = trade.get("buyer")
                    seller = trade.get("seller")
                    if buyer:
                        unique_holders.add(buyer)
                    if seller:
                        unique_holders.add(seller)

                # 檢查是否還有更多數據
                if data.get("has_more") and data.get("cursor"):
                    cursor = data.get("cursor")  # 更新 cursor，繼續查詢
                    print(f"🔄 發現更多交易數據，繼續加載... (已處理 {total_trades} 筆交易)")
                else:
                    break  # 沒有更多數據時結束查詢
            else:
                print("❌ 無法解析 API 回應數據，請檢查 Mint 地址是否正確。")
                break
        elif response.status_code == 404:
            print("❌ 找不到該代幣的交易數據，請檢查 Mint 地址是否正確。")
            break
        else:
            print(f"❌ API 請求失敗: {response.status_code}")
            print(response.text)
            break
        
        iteration += 1  # 增加迴圈次數，防止無限循環

    print(f"✅ 完成查詢！總共處理 {total_trades} 筆交易")
    print(f"✅ 該代幣的持有人數（基於歷史交易）: {len(unique_holders)}")

if __name__ == "__main__":
    get_holders_from_trades()

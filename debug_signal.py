import requests

r = requests.get("https://open-api.bingx.com/openApi/swap/v3/quote/klines",
    params={"symbol":"BTC-USDT","interval":"1h","limit":5}, timeout=15)

print("STATUS:", r.status_code)
print("RESPUESTA:", r.text[:500])
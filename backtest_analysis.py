"""
Análisis de rentabilidad — EMA 2/4/20 en múltiples pares
Descarga datos reales de BingX y calcula métricas profesionales
"""
import os, sys, time, math
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0,".")
from bingx_client import BingXClient

KEY    = os.environ.get("BINGX_API_KEY","")
SECRET = os.environ.get("BINGX_API_SECRET","")

PAIRS     = ["BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT",
             "AVAX-USDT","DOGE-USDT","LINK-USDT","ARB-USDT","SUI-USDT"]
INTERVAL  = "3m"
LEVERAGE  = 5
RISK_PCT  = 0.01      # 1% por trade
SL_ATR    = 1.5
TP1_R     = 1.0
TP2_R     = 2.5
INITIAL   = 1000.0

def _ema(s,p):  return s.ewm(span=p,adjust=False).mean()

def _atr(df,p=14):
    h,lo,c=df["high"],df["low"],df["close"]; pc=c.shift(1)
    tr=pd.concat([h-lo,(h-pc).abs(),(lo-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(com=p-1,adjust=False).mean()

def run_pair(df: pd.DataFrame, symbol: str) -> dict:
    if len(df) < 50: return None
    c  = df["close"].copy()
    e1 = _ema(c,2); e2 = _ema(c,4); e3 = _ema(c,20)
    atr = _atr(df)

    long_a  = (c.shift(1)>=e3.shift(1)) & (c<e3)
    long_b  = (c.diff()<0)&(e1.diff()<0)&(c.shift(1)>=e1.shift(1))&(c<e1)&(e2.diff()>0)
    short_a = (c.shift(1)<=e3.shift(1)) & (c>e3)
    short_b = (c.diff()>0)&(e1.diff()>0)&(c.shift(1)<=e1.shift(1))&(c>e1)&(e2.diff()<0)

    df = df.copy()
    df["long"]  = long_a | long_b
    df["short"] = short_a | short_b
    df["atr"]   = atr

    balance = INITIAL
    equity  = [INITIAL]
    trades  = []
    pos     = None

    for i in range(20, len(df)-1):
        row  = df.iloc[i]
        nxt  = df.iloc[i+1]
        price= float(row["close"])
        atv  = float(row["atr"]) if not math.isnan(float(row["atr"])) else price*0.005

        # Gestionar posición abierta
        if pos:
            hi = float(nxt["high"]); lo2 = float(nxt["low"])
            hit_sl = hit_tp1 = hit_tp2 = False
            if pos["side"]=="LONG":
                if lo2 <= pos["sl"]:  hit_sl  = True
                if hi  >= pos["tp1"] and not pos["tp1_hit"]: hit_tp1 = True
                if hi  >= pos["tp2"]: hit_tp2 = True
            else:
                if hi  >= pos["sl"]:  hit_sl  = True
                if lo2 <= pos["tp1"] and not pos["tp1_hit"]: hit_tp1 = True
                if lo2 <= pos["tp2"]: hit_tp2 = True

            if hit_tp1 and not pos["tp1_hit"]:
                pos["tp1_hit"] = True
                pos["sl"]      = pos["entry"]  # breakeven
                half_pnl = pos["risk"] * TP1_R
                balance += half_pnl
                pos["partial_pnl"] = half_pnl

            if hit_tp2:
                pnl = pos["risk"] * TP2_R + pos.get("partial_pnl",0)
                balance += pos["risk"] * TP2_R
                trades.append({"pnl": pnl, "side": pos["side"], "result":"TP"})
                pos = None
            elif hit_sl:
                loss = -pos["risk"] * (0 if pos["tp1_hit"] else 1)
                balance += loss
                trades.append({"pnl": pos.get("partial_pnl",0)+loss,
                               "side": pos["side"], "result":"SL"})
                pos = None

        equity.append(balance)

        # Nueva entrada
        if not pos:
            if row["long"]:
                risk  = balance * RISK_PCT
                sl_d  = atv * SL_ATR
                pos   = {"side":"LONG","entry":price,
                         "sl":price-sl_d,"tp1":price+sl_d*TP1_R,"tp2":price+sl_d*TP2_R,
                         "risk":risk,"tp1_hit":False,"partial_pnl":0}
            elif row["short"]:
                risk  = balance * RISK_PCT
                sl_d  = atv * SL_ATR
                pos   = {"side":"SHORT","entry":price,
                         "sl":price+sl_d,"tp1":price-sl_d*TP1_R,"tp2":price-sl_d*TP2_R,
                         "risk":risk,"tp1_hit":False,"partial_pnl":0}

    if not trades: return None

    tdf   = pd.DataFrame(trades)
    wins  = tdf[tdf["pnl"]>0]
    loss  = tdf[tdf["pnl"]<=0]
    eq    = pd.Series(equity)
    dd    = ((eq.cummax()-eq)/eq.cummax().replace(0,np.nan)).max()
    gross_profit = wins["pnl"].sum()  if len(wins) else 0
    gross_loss   = abs(loss["pnl"].sum()) if len(loss) else 0.0001

    return {
        "symbol":   symbol,
        "trades":   len(tdf),
        "win_rate": len(wins)/len(tdf)*100,
        "pnl":      tdf["pnl"].sum(),
        "return%":  (balance-INITIAL)/INITIAL*100,
        "max_dd%":  dd*100,
        "pf":       gross_profit/gross_loss,
        "avg_win":  wins["pnl"].mean()   if len(wins) else 0,
        "avg_loss": loss["pnl"].mean()   if len(loss) else 0,
        "balance":  balance,
    }


def main():
    if not KEY or not SECRET:
        print("Necesitas BINGX_API_KEY y BINGX_API_SECRET en .env")
        sys.exit(1)

    client  = BingXClient(KEY, SECRET, demo=True)
    results = []

    print(f"\nAnalizando {len(PAIRS)} pares en {INTERVAL}...\n")

    for symbol in PAIRS:
        try:
            # Obtener ~500 velas (aprox 25 horas en 3m)
            klines = client.get_klines(symbol, INTERVAL, limit=500)
            if not klines or len(klines) < 50:
                print(f"  {symbol}: sin datos"); continue

            df = pd.DataFrame(klines)
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            for col in ["open","high","low","close","volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna(subset=["close"]).sort_values("timestamp").reset_index(drop=True)

            r = run_pair(df, symbol)
            if r:
                results.append(r)
                print(f"  ✅ {symbol}: {r['trades']} trades | WR={r['win_rate']:.0f}% | "
                      f"PnL={r['pnl']:+.2f}$ | DD={r['max_dd%']:.1f}%")
            time.sleep(0.2)
        except Exception as e:
            print(f"  ❌ {symbol}: {e}")

    if not results:
        print("Sin resultados"); return

    df_r = pd.DataFrame(results)
    df_r = df_r.sort_values("pnl", ascending=False)

    print("\n" + "="*65)
    print(f"  RESUMEN — EMA 2/4/20 | {INTERVAL} | Apalancamiento {LEVERAGE}x")
    print("="*65)
    print(f"  Pares analizados:     {len(results)}")
    print(f"  Total trades:         {df_r['trades'].sum()}")
    print(f"  Win rate promedio:    {df_r['win_rate'].mean():.1f}%")
    print(f"  PnL total:            {df_r['pnl'].sum():+.2f}$")
    print(f"  Mejor par:            {df_r.iloc[0]['symbol']} ({df_r.iloc[0]['pnl']:+.2f}$)")
    print(f"  Peor par:             {df_r.iloc[-1]['symbol']} ({df_r.iloc[-1]['pnl']:+.2f}$)")
    print(f"  Profit Factor prom:   {df_r['pf'].mean():.2f}")
    print(f"  Max DD promedio:      {df_r['max_dd%'].mean():.1f}%")
    print("="*65)
    print("\nDetalle por par:")
    print(f"{'Par':<18} {'Trades':>7} {'WR%':>6} {'PnL$':>8} {'PF':>6} {'DD%':>6}")
    print("-"*55)
    for _, row in df_r.iterrows():
        print(f"{row['symbol']:<18} {row['trades']:>7} {row['win_rate']:>5.0f}% "
              f"{row['pnl']:>+8.2f} {row['pf']:>6.2f} {row['max_dd%']:>5.1f}%")

    df_r.to_csv("backtest_results.csv", index=False)
    print("\n✅ Resultados guardados en backtest_results.csv")
    return df_r

if __name__ == "__main__":
    main()

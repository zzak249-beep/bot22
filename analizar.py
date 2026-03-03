import exchange as ex

ex2 = ex.get_exchange()
trades = ex2.fetch_orders(limit=50)

wins, losses, total_pnl = 0, 0, 0.0
pnl_list = []

for t in trades:
    if t['status'] == 'closed':
        pnl = float(t.get('info', {}).get('profit', 0) or 0)
        if pnl != 0:
            pnl_list.append(pnl)
            total_pnl += pnl
            if pnl > 0: wins += 1
            else: losses += 1

total = wins + losses
print(f"\n{'='*40}")
print(f"Trades analizados : {total}")
print(f"Ganadores         : {wins}")
print(f"Perdedores        : {losses}")
print(f"Win rate          : {wins/total*100:.1f}%" if total > 0 else "Sin datos")
print(f"PnL total         : ${total_pnl:+.2f}")
print(f"Mejor trade       : ${max(pnl_list):+.2f}" if pnl_list else "")
print(f"Peor trade        : ${min(pnl_list):+.2f}" if pnl_list else "")
print(f"{'='*40}")
import config as cfg
print(f"\nLeverage configurado : {cfg.LEVERAGE}x")
print(f"Riesgo por trade     : {cfg.RISK_PCT*100:.1f}%")
print(f"Max posiciones       : {cfg.MAX_POSITIONS}")
print(f"Balance actual       : ${ex.get_balance():.4f}")
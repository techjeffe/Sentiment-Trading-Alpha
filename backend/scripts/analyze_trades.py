"""Trade analysis script - queries the trading system database and outputs performance metrics.
Analyzes: recommendation trades, Alpaca orders (live), paper trades, decision log trades."""
import sqlite3
import os
from pathlib import Path
from datetime import datetime

# First, list all tables in the main database
db_path = Path(__file__).parent / 'trading_system.db'
conn = sqlite3.connect(str(db_path))
cur = conn.cursor()
cur.execute('SELECT name FROM sqlite_master WHERE type="table"')
all_tables = [r[0] for r in cur.fetchall()]
print("=== ALL TABLES IN trading_system.db ===")
for t in all_tables:
    print(f"  - {t}")

# Check for additional trade-related tables
cur.execute('''
    SELECT name FROM sqlite_master 
    WHERE type="table" AND (
        name LIKE '%alpaca%' OR 
        name LIKE '%order%' OR 
        name LIKE '%execution%' OR 
        name LIKE '%position%'
    )
''')
extra_tables = [r[0] for r in cur.fetchall()]
for t in extra_tables:
    if t not in all_tables:
        all_tables.append(t)
        print(f"  - {t} (found via search)")

# Also check for decision_log.db
decision_db_path = Path(__file__).parent / 'decision_log.db'
dec_tables = []
dconn = None
dcur = None
if os.path.exists(decision_db_path):
    dconn = sqlite3.connect(decision_db_path)
    dcur = dconn.cursor()
    dcur.execute('SELECT name FROM sqlite_master WHERE type="table"')
    dec_tables = [r[0] for r in dcur.fetchall()]
    print(f"\n=== ALL TABLES IN decision_log.db ===")
    for t in dec_tables:
        print(f"  - {t}")
else:
    print(f"\ndecision_log.db not found at {decision_db_path}")

conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Check table counts - only for tables that exist
print("\n=== TABLE ROW COUNTS ===")
candidate_tables = ['trades', 'paper_trades', 'alpaca_orders', 'trade_snapshots', 
                    'trade_executions', 'trade_closes', 'analysis_results', 'trading_signals']
for table in candidate_tables:
    if table in all_tables:
        cur.execute(f'SELECT COUNT(*) as cnt FROM {table}')
        row = cur.fetchone()
        print(f"  {table}: {row['cnt']} rows")
    else:
        print(f"  {table}: TABLE NOT FOUND")

# Decision log tables
if dcur is not None:
    dec_candidate_tables = ['decision_log_run', 'decision_log_symbol', 'decision_log_trade', 
                            'decision_log_trade_event', 'decision_log_article', 'decision_log_technical']
    print("\n=== DECISION LOG TABLE ROW COUNTS ===")
    for table in dec_candidate_tables:
        if table in dec_tables:
            dcur.execute(f'SELECT COUNT(*) as cnt FROM {table}')
            row = dcur.fetchone()
            print(f"  {table}: {row[0]} rows")
        else:
            print(f"  {table}: TABLE NOT FOUND")

# ============================================================
# ANALYZE RECOMMENDATION TRADES TABLE
# ============================================================
if 'trades' in all_tables:
    print("\n=== TRADES TABLE (Recommendation Trades) ===")
    cur.execute('SELECT COUNT(*) as total FROM trades')
    total = cur.fetchone()['total']
    cur.execute("SELECT COUNT(*) as buys FROM trades WHERE action='BUY'")
    buys = cur.fetchone()['buys']
    cur.execute("SELECT COUNT(*) as sells FROM trades WHERE action='SELL'")
    sells = cur.fetchone()['sells']
    print(f"  Total: {total}, Buys: {buys}, Sells: {sells}")

    print("\n=== RECENT RECOMMENDATION TRADES ===")
    cur.execute('''
        SELECT symbol, action, leverage, confidence_score, entry_price, 
               recommended_at, conviction_level, trading_type, holding_period_hours, underlying_symbol
        FROM trades
        ORDER BY recommended_at DESC
        LIMIT 20
    ''')
    recent_trades = cur.fetchall()
    for r in recent_trades:
        print(f"  {r['symbol']} ({r['underlying_symbol']}) {r['action']} {r['leverage']} "
              f"conf={r['confidence_score']:.2f} @ ${r['entry_price']:.2f} "
              f"({r['conviction_level']}, {r['trading_type']}, {r['holding_period_hours']}h) "
              f"- {r['recommended_at']}")

    print("\n=== CONFIDENCE SCORE DISTRIBUTION ===")
    cur.execute('''
        SELECT 
            CASE 
                WHEN confidence_score >= 0.8 THEN 'HIGH (>=0.8)'
                WHEN confidence_score >= 0.6 THEN 'MEDIUM (0.6-0.8)'
                ELSE 'LOW (<0.6)'
            END as bucket,
            COUNT(*) as count,
            AVG(confidence_score) as avg_conf
        FROM trades
        GROUP BY bucket
    ''')
    for r in cur.fetchall():
        print(f"  {r['bucket']}: {r['count']} trades, avg conf={r['avg_conf']:.2f}")

    print("\n=== CONVICTON LEVEL DISTRIBUTION ===")
    cur.execute('''
        SELECT 
            conviction_level,
            COUNT(*) as count
        FROM trades
        GROUP BY conviction_level
    ''')
    for r in cur.fetchall():
        print(f"  {r['conviction_level']}: {r['count']} trades")

    print("\n=== TRADING TYPE DISTRIBUTION ===")
    cur.execute('''
        SELECT 
            trading_type,
            COUNT(*) as count,
            AVG(holding_period_hours) as avg_hold_hours
        FROM trades
        GROUP BY trading_type
    ''')
    for r in cur.fetchall():
        print(f"  {r['trading_type']}: {r['count']} trades, avg hold={r['avg_hold_hours']:.1f}h")

# ============================================================
# ANALYZE PAPER TRADES (if table exists)
# ============================================================
if 'paper_trades' in all_tables:
    print("\n=== PAPER TRADES SUMMARY ===")
    cur.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN exit_price IS NOT NULL THEN 1 ELSE 0 END) as closed,
            SUM(CASE WHEN exit_price IS NULL THEN 1 ELSE 0 END) as open,
            SUM(CASE WHEN realized_pnl IS NOT NULL AND realized_pnl > 0 THEN 1 ELSE 0 END) as winners,
            SUM(CASE WHEN realized_pnl IS NOT NULL AND realized_pnl < 0 THEN 1 ELSE 0 END) as losers
        FROM paper_trades
    ''')
    row = cur.fetchone()
    print(f"  Total: {row['total']}, Closed: {row['closed']}, Open: {row['open']}")
    print(f"  Winners: {row['winners']}, Losers: {row['losers']}")

    print("\n=== PAPER TRADES PnL STATS ===")
    cur.execute('''
        SELECT 
            AVG(realized_pnl) as avg_pnl,
            SUM(realized_pnl) as total_pnl,
            MIN(realized_pnl) as min_pnl,
            MAX(realized_pnl) as max_pnl,
            AVG(realized_pnl_pct) as avg_pnl_pct
        FROM paper_trades 
        WHERE exit_price IS NOT NULL AND realized_pnl IS NOT NULL
    ''')
    row = cur.fetchone()
    print(f"  Avg PnL: ${row['avg_pnl']:.2f}")
    print(f"  Total PnL: ${row['total_pnl']:.2f}")
    print(f"  Min PnL: ${row['min_pnl']:.2f}")
    print(f"  Max PnL: ${row['max_pnl']:.2f}")
    print(f"  Avg PnL%: {row['avg_pnl_pct']:.2f}%")

# ============================================================
# ANALYZE ALPACA ORDERS (LIVE TRADING DATA)
# ============================================================
if 'alpaca_orders' in all_tables:
    print("\n" + "="*70)
    print("=== ALPACA LIVE TRADING DATA ===")
    print("="*70)
    cur.execute('SELECT COUNT(*) as cnt FROM alpaca_orders')
    alpaca_total = cur.fetchone()['cnt']
    print(f"\n  Total Alpaca orders: {alpaca_total}")
    
    if alpaca_total > 0:
        # Status breakdown
        cur.execute('''
            SELECT status, COUNT(*) as count
            FROM alpaca_orders
            GROUP BY status
            ORDER BY count DESC
        ''')
        print("\n  ORDER STATUS BREAKDOWN:")
        for r in cur.fetchall():
            print(f"    {r['status']}: {r['count']}")

        # All filled orders (not just LIMIT 10)
        cur.execute('''
            SELECT symbol, side, notional, filled_avg_price, filled_qty, 
                   created_at, submitted_at, filled_at, trading_mode,
                   order_class, exits
            FROM alpaca_orders 
            WHERE status = 'filled'
            ORDER BY filled_at DESC
        ''')
        filled_orders = cur.fetchall()
        print(f"\n  FILLED ORDERS ({len(filled_orders)} total):")
        for r in filled_orders:
            filled_date = r['filled_at'][:16] if r['filled_at'] else 'N/A'
            print(f"    {r['symbol']} {r['side']} {r['filled_qty']} shares @ ${r['filled_avg_price']:.2f} "
                  f"(${r['notional']:.0f} notional) [{r['trading_mode']}] filled={filled_date}")

        # Symbol breakdown for filled orders
        cur.execute('''
            SELECT symbol, side, 
                   COUNT(*) as fill_count,
                   SUM(filled_qty) as total_qty,
                   AVG(filled_avg_price) as avg_price,
                   MIN(filled_at) as first_fill,
                   MAX(filled_at) as last_fill
            FROM alpaca_orders 
            WHERE status = 'filled'
            GROUP BY symbol, side
            ORDER BY fill_count DESC
        ''')
        print(f"\n  FILLED ORDERS BY SYMBOL:")
        for r in cur.fetchall():
            print(f"    {r['symbol']} {r['side']}: {r['fill_count']} fills, "
                  f"total_qty={r['total_qty']:.4f}, avg_price=${r['avg_price']:.2f}, "
                  f"first={r['first_fill'][:16] if r['first_fill'] else 'N/A'}, "
                  f"last={r['last_fill'][:16] if r['last_fill'] else 'N/A'}")

        # Check for exit orders (closes)
        cur.execute('''
            SELECT symbol, side, notional, filled_avg_price, filled_qty, 
                   created_at, status, order_type
            FROM alpaca_orders 
            WHERE side = 'sell' AND status = 'filled'
            ORDER BY created_at DESC
            LIMIT 30
        ''')
        sells = cur.fetchall()
        if sells:
            print(f"\n  RECENT SELL ORDERS (exits):")
            for r in sells:
                print(f"    {r['symbol']} SELL {r['filled_qty']:.4f} shares @ ${r['filled_avg_price']:.2f} "
                      f"(${r['notional']:.0f}) [{r['order_type']}] at {r['created_at']}")

        # Detect round-trip trades: buy + sell pairs
        print(f"\n  ROUND-TRIP TRADE ANALYSIS:")
        cur.execute('''
            SELECT symbol, 
                   SUM(CASE WHEN side = 'buy' THEN 1 ELSE 0 END) as buy_count,
                   SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) as sell_count
            FROM alpaca_orders 
            WHERE status = 'filled'
            GROUP BY symbol
            HAVING buy_count > 0 AND sell_count > 0
            ORDER BY symbol
        ''')
        round_trips = cur.fetchall()
        for r in round_trips:
            print(f"    {r['symbol']}: {r['buy_count']} buys + {r['sell_count']} sells = round-trip candidate")

conn2 = sqlite3.connect(str(db_path))
conn2.row_factory = sqlite3.Row
cur2 = conn2.cursor()

# ============================================================
# DETAILED ALPACA ROUND-TRIP PnL ANALYSIS
# ============================================================
if 'alpaca_orders' in all_tables:
    print("\n" + "="*70)
    print("=== ALPACA ROUND-TRIP PnL ANALYSIS ===")
    print("="*70)

    # Get all filled buy orders
    cur2.execute('''
        SELECT id, symbol, side, filled_qty, filled_avg_price as entry_price,
               filled_at as entry_time, notional, trading_mode
        FROM alpaca_orders 
        WHERE status = 'filled' AND side = 'buy'
        ORDER BY symbol, filled_at
    ''')
    alpaca_buys = cur2.fetchall()

    # Get all filled sell orders
    cur2.execute('''
        SELECT id, symbol, side, filled_qty, filled_avg_price as exit_price,
               filled_at as exit_time, notional, trading_mode
        FROM alpaca_orders 
        WHERE status = 'filled' AND side = 'sell'
        ORDER BY symbol, filled_at
    ''')
    alpaca_sells = cur2.fetchall()

    # Build round-trip PnL estimates
    # Group sells by symbol
    from collections import defaultdict
    alpaca_symbol_sells = defaultdict(list)
    alpaca_symbol_buys = defaultdict(list)

    print(f"\n  SYMBOLS WITH BOTH BUYS AND SELLS:")
    total_est_pnl = 0
    total_est_notional = 0

    for symbol in sorted(alpaca_symbol_buys.keys()):
        if symbol in alpaca_symbol_sells:
            b_list = alpaca_symbol_buys[symbol]
            s_list = alpaca_symbol_sells[symbol]
            
            # Match buys to sells (FIFO)
            buy_queue = list(b_list)  # already sorted by time
            sell_list = list(s_list)  # already sorted by time
            
            symbol_pnl = 0
            symbol_trades = 0
            
            for sell in sell_list:
                # Find matching buy
                remaining_qty = sell['filled_qty']
                while remaining_qty > 0 and buy_queue:
                    buy = buy_queue[0]
                    fill_qty = min(remaining_qty, buy['filled_qty'])
                    
                    # PnL calculation
                    pnl = (sell['exit_price'] - buy['entry_price']) * fill_qty
                    notional = buy['entry_price'] * fill_qty
                    pnl_pct = ((sell['exit_price'] - buy['entry_price']) / buy['entry_price']) * 100
                    
                    symbol_pnl += pnl
                    total_est_notional += notional
                    symbol_trades += 1
                    
                    remaining_qty -= fill_qty
                    buy_queue[0]['filled_qty'] -= fill_qty
                    if buy_queue[0]['filled_qty'] <= 0:
                        buy_queue.pop(0)
            
            total_est_pnl += symbol_pnl
            win_rate = sum(1 for s in s_list 
                          for b in [next((x for x in alpaca_symbol_buys[symbol] if x['entry_price'] < s['exit_price']), None)] 
                          if b is not None) / len(s_list) * 100 if s_list else 0
            
            print(f"\n    {symbol}:")
            print(f"      Estimated PnL: ${symbol_pnl:+.2f}")
            print(f"      Sell orders: {len(sell_list)}")
            print(f"      Avg entry: ${sum(b['entry_price'] for b in b_list)/len(b_list):.2f}")
            print(f"      Avg exit: ${sum(s['exit_price'] for s in s_list)/len(s_list):.2f}")

    print(f"\n  OVERALL ESTIMATED PnL: ${total_est_pnl:+.2f}")
    print(f"  Total notional: ${total_est_notional:,.0f}")
else:
    print("\n  No Alpaca orders table - skipping round-trip analysis")

conn2.close()

# ============================================================
# ANALYZE DECISION LOG TRADES (if DB exists)
# ============================================================
if dcur is not None and 'decision_log_trade' in dec_tables:
    print("\n=== DECISION LOG TRADES ===")
    dcur.execute('SELECT COUNT(*) as cnt FROM decision_log_trade')
    dl_trade_count = dcur.fetchone()[0]
    print(f"  Total trades logged: {dl_trade_count}")

    # Realized PnL stats
    dcur.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN realized_pnl IS NOT NULL AND realized_pnl > 0 THEN 1 ELSE 0 END) as winners,
            SUM(CASE WHEN realized_pnl IS NOT NULL AND realized_pnl < 0 THEN 1 ELSE 0 END) as losers,
            SUM(CASE WHEN realized_pnl IS NOT NULL THEN 1 ELSE 0 END) as has_pnl,
            AVG(realized_pnl) as avg_pnl,
            SUM(realized_pnl) as total_pnl,
            MIN(realized_pnl) as min_pnl,
            MAX(realized_pnl) as max_pnl
        FROM decision_log_trade
        WHERE realized_pnl IS NOT NULL
    ''')
    row = dcur.fetchone()
    total_trades = row[0]
    winners = row[1]
    losers = row[2]
    
    print(f"\n  PnL Statistics (trades with realized PnL):")
    print(f"    Total with PnL: {total_trades}")
    print(f"    Winners: {winners}, Losers: {losers}")
    if total_trades > 0:
        print(f"    Win Rate: {winners*100.0/total_trades:.1f}%")
    print(f"    Avg PnL: ${row[4]:.2f}")
    print(f"    Total PnL: ${row[5]:.2f}")
    print(f"    Min PnL: ${row[6]:.2f}")
    print(f"    Max PnL: ${row[7]:.2f}")

    # By symbol
    print("\n  PERFORMANCE BY SYMBOL:")
    dcur.execute('''
        SELECT 
            symbol,
            COUNT(*) as trade_count,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
            AVG(realized_pnl) as avg_pnl,
            SUM(realized_pnl) as total_pnl
        FROM decision_log_trade
        WHERE realized_pnl IS NOT NULL
        GROUP BY symbol
        ORDER BY total_pnl DESC
    ''')
    for r in dcur.fetchall():
        trade_count = r[1]
        win_rate = r[2]*100.0/trade_count if trade_count > 0 else 0
        print(f"    {r['symbol']}: {trade_count} trades, {win_rate:.0f}% win rate "
              f"({r[2]}W/{r[3]}L), avg ${r[4]:.2f}, total ${r[5]:.2f}")

    # By direction
    print("\n  PERFORMANCE BY DIRECTION:")
    dcur.execute('''
        SELECT 
            direction,
            COUNT(*) as trade_count,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
            AVG(realized_pnl) as avg_pnl,
            SUM(realized_pnl) as total_pnl
        FROM decision_log_trade
        WHERE realized_pnl IS NOT NULL
        GROUP BY direction
    ''')
    for r in dcur.fetchall():
        trade_count = r[1]
        win_rate = r[2]*100.0/trade_count if trade_count > 0 else 0
        print(f"    {r['direction']}: {trade_count} trades, {win_rate:.0f}% win rate "
              f"({r[2]}W/{r[3]}L), avg ${r[4]:.2f}, total ${r[5]:.2f}")

    # Close triggers
    print("\n  CLOSE TRIGGERS:")
    dcur.execute('''
        SELECT 
            close_trigger,
            COUNT(*) as count,
            AVG(realized_pnl) as avg_pnl,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses
        FROM decision_log_trade
        WHERE realized_pnl IS NOT NULL
        GROUP BY close_trigger
        ORDER BY count DESC
    ''')
    for r in dcur.fetchall():
        print(f"    {r['close_trigger']}: {r['count']} trades, avg ${r['avg_pnl']:.2f}, {r[3]}W/{r[4]}L")

    # Top winners
    print("\n  TOP 5 WINNING TRADES:")
    dcur.execute('''
        SELECT symbol, direction, entry_price, close_price, entry_timestamp, close_timestamp,
               realized_pnl, entry_directional_score, entry_confidence, close_trigger
        FROM decision_log_trade
        WHERE realized_pnl IS NOT NULL
        ORDER BY realized_pnl DESC
        LIMIT 5
    ''')
    for r in dcur.fetchall():
        print(f"    {r['symbol']} {r['direction']}: ${r['realized_pnl']:.2f} "
              f"(entry ${r['entry_price']:.2f} -> close ${r['close_price']:.2f}), "
              f"trigger: {r['close_trigger']}, score: {r['entry_directional_score']:.2f}")

    # Top losers
    print("\n  TOP 5 LOSING TRADES:")
    dcur.execute('''
        SELECT symbol, direction, entry_price, close_price, entry_timestamp, close_timestamp,
               realized_pnl, entry_directional_score, entry_confidence, close_trigger
        FROM decision_log_trade
        WHERE realized_pnl IS NOT NULL
        ORDER BY realized_pnl ASC
        LIMIT 5
    ''')
    for r in dcur.fetchall():
        print(f"    {r['symbol']} {r['direction']}: ${r['realized_pnl']:.2f} "
              f"(entry ${r['entry_price']:.2f} -> close ${r['close_price']:.2f}), "
              f"trigger: {r['close_trigger']}, score: {r['entry_directional_score']:.2f}")

    # Holding period analysis
    print("\n  HOLDING PERIOD ANALYSIS:")
    dcur.execute('''
        SELECT 
            symbol,
            COUNT(*) as trade_count,
            AVG(julianday(close_timestamp) - julianday(entry_timestamp)) * 24 as avg_hold_hours,
            MIN(julianday(close_timestamp) - julianday(entry_timestamp)) * 24 as min_hold_hours,
            MAX(julianday(close_timestamp) - julianday(entry_timestamp)) * 24 as max_hold_hours,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
            SUM(realized_pnl) as total_pnl
        FROM decision_log_trade
        WHERE close_timestamp IS NOT NULL AND realized_pnl IS NOT NULL
        GROUP BY symbol
        ORDER BY total_pnl DESC
    ''')
    for r in dcur.fetchall():
        trade_count = r[1]
        win_rate = r[5]*100.0/trade_count if trade_count > 0 else 0
        print(f"    {r['symbol']}: {trade_count} trades, {win_rate:.0f}% win rate, "
              f"avg hold={r[2]:.1f}h (min={r[3]:.1f}h, max={r[4]:.1f}h), total ${r[7]:.2f}")

    # Decision log symbol analysis - look at signal patterns
    if 'decision_log_symbol' in dec_tables:
        print("\n  SIGNAL TYPE DISTRIBUTION (from decision log):")
        dcur.execute('''
            SELECT 
                final_signal_type,
                COUNT(*) as count,
                AVG(blended_confidence_score) as avg_confidence
            FROM decision_log_symbol
            WHERE final_signal_type IS NOT NULL
            GROUP BY final_signal_type
            ORDER BY count DESC
        ''')
        for r in dcur.fetchall():
            print(f"    {r['final_signal_type']}: {r['count']} signals, avg confidence={r['avg_confidence']:.2f}")

        # Conviction distribution
        print("\n  CONVICTION LEVEL DISTRIBUTION:")
        dcur.execute('''
            SELECT 
                final_conviction,
                COUNT(*) as count,
                AVG(blended_directional_score) as avg_directional_score
            FROM decision_log_symbol
            WHERE final_conviction IS NOT NULL
            GROUP BY final_conviction
            ORDER BY 
                CASE final_conviction
                    WHEN 'HIGH' THEN 1
                    WHEN 'MEDIUM' THEN 2
                    WHEN 'LOW' THEN 3
                    ELSE 4
                END
        ''')
        for r in dcur.fetchall():
            print(f"    {r['final_conviction']}: {r['count']} signals, avg directional={r['avg_directional_score']:.2f}")

        # Look at stop loss / take profit settings
        print("\n  STOP LOSS / TAKE PROFIT SETTINGS:")
        dcur.execute('''
            SELECT 
                COUNT(*) as total,
                AVG(final_stop_loss_pct) as avg_stop_loss,
                AVG(final_take_profit_pct) as avg_take_profit,
                SUM(CASE WHEN final_stop_loss_pct IS NOT NULL THEN 1 ELSE 0 END) as has_stop_loss,
                SUM(CASE WHEN final_take_profit_pct IS NOT NULL THEN 1 ELSE 0 END) as has_take_profit
            FROM decision_log_symbol
        ''')
        row = dcur.fetchone()
        total = row[0]
        print(f"    Total signals: {total}")
        print(f"    With stop loss: {row[3]} ({row[3]*100.0/total:.0f}%), avg: {row[1]*100:.1f}%")
        print(f"    With take profit: {row[4]} ({row[4]*100.0/total:.0f}%), avg: {row[2]*100:.1f}%")

        # ATR analysis
        print("\n  ATR (Average True Range) USAGE:")
        dcur.execute('''
            SELECT 
                COUNT(*) as total,
                AVG(atr_14d_pct) as avg_atr,
                MIN(atr_14d_pct) as min_atr,
                MAX(atr_14d_pct) as max_atr
            FROM decision_log_symbol
            WHERE atr_14d_pct IS NOT NULL
        ''')
        row = dcur.fetchone()
        print(f"    Signals with ATR data: {row[0]}")
        print(f"    Avg ATR: {row[1]:.2f}%, Range: {row[2]:.2f}% - {row[3]:.2f}%")

        # Regime adaptation
        print("\n  REGIME ADAPTATION TRIGGERED:")
        dcur.execute('''
            SELECT 
                SUM(CASE WHEN regime_adaptation_triggered IS NOT NULL AND regime_adaptation_triggered > 0 THEN 1 ELSE 0 END) as triggered,
                COUNT(*) as total
            FROM decision_log_symbol
        ''')
        row = dcur.fetchone()
        print(f"    Regime adaptation triggered: {row[0]} out of {row[1]} signals ({row[0]*100.0/row[1]:.0f}%)")

        # Red team disagreement
        print("\n  RED TEAM DISAGREEMENT:")
        dcur.execute('''
            SELECT 
                SUM(CASE WHEN red_team_disagreed IS NOT NULL AND red_team_disagreed > 0 THEN 1 ELSE 0 END) as disagreed,
                COUNT(*) as total,
                AVG(red_team_confidence_delta) as avg_confidence_delta
            FROM decision_log_symbol
            WHERE red_team_disagreed IS NOT NULL
        ''')
        row = dcur.fetchone()
        print(f"    Red team disagreed: {row[0]} out of {row[1]} ({row[0]*100.0/row[1]:.0f}%)")
        print(f"    Avg confidence delta: {row[2]:.3f}")

        # Technical indicators usage
        if 'decision_log_technical' in dec_tables:
            print("\n  TECHNICAL INDICATORS AVERAGE VALUES:")
            dcur.execute('''
                SELECT 
                    AVG(rsi_14) as avg_rsi,
                    SUM(CASE WHEN golden_cross = 1 THEN 1 ELSE 0 END) as golden_cross_count,
                    SUM(CASE WHEN death_cross = 1 THEN 1 ELSE 0 END) as death_cross_count,
                    SUM(CASE WHEN macd_positive = 1 THEN 1 ELSE 0 END) as macd_positive_count,
                    SUM(CASE WHEN volume_above_average = 1 THEN 1 ELSE 0 END) as volume_above_avg_count
                FROM decision_log_technical
            ''')
            row = dcur.fetchone()
            print(f"    Avg RSI(14): {row[0]:.1f}")
            print(f"    Golden crosses: {row[1]}")
            print(f"    Death crosses: {row[2]}")
            print(f"    MACD positive: {row[3]}")
            print(f"    Volume above average: {row[4]}")

# ============================================================
# TRADE SNAPSHOTS (forward-looking PnL)
# ============================================================
if 'trade_snapshots' in all_tables:
    print("\n=== TRADE SNAPSHOTS (Forward-Looking PnL) ===")
    cur.execute('''
        SELECT 
            horizon_label,
            COUNT(*) as count,
            AVG(raw_return_pct) as avg_raw_return,
            AVG(leveraged_return_pct) as avg_lev_return,
            SUM(CASE WHEN raw_return_pct > 0 THEN 1 ELSE 0 END) as positive,
            SUM(CASE WHEN raw_return_pct < 0 THEN 1 ELSE 0 END) as negative
        FROM trade_snapshots
        GROUP BY horizon_label
        ORDER BY 
            CASE horizon_label
                WHEN '1h' THEN 1
                WHEN '4h' THEN 2
                WHEN '1d' THEN 3
                WHEN '3d' THEN 4
                WHEN '1w' THEN 5
            END
    ''')
    print("  Performance by time horizon:")
    for r in cur.fetchall():
        total = r[1]
        win_rate = r[4]*100.0/total if total > 0 else 0
        print(f"    {r['horizon_label']}: {total} snapshots, {win_rate:.0f}% positive, "
              f"avg raw return={r[2]:.2f}%, avg leveraged return={r[3]:.2f}%")

conn.close()
if dconn:
    dconn.close()

# ============================================================
# DETAILED RECOMMENDATION TRADE ANALYSIS (from trades table)
# ============================================================
conn3 = sqlite3.connect(str(db_path))
conn3.row_factory = sqlite3.Row
cur3 = conn3.cursor()

print("\n=== ALL RECOMMENDATION TRADES FULL DETAILS ===")
cur3.execute('''
    SELECT id, symbol, underlying_symbol, action, leverage, signal_type, confidence_score,
           entry_price, entry_price_timestamp, stop_loss_pct, take_profit_pct,
           recommended_at, conviction_level, holding_period_hours, trading_type, holding_window_until
    FROM trades
    ORDER BY recommended_at
''')
for r in cur3.fetchall():
    sl_str = f"{r['stop_loss_pct']}%" if r['stop_loss_pct'] else 'None'
    tp_str = f"{r['take_profit_pct']}%" if r['take_profit_pct'] else 'None'
    print(f"  #{r['id']} {r['symbol']} ({r['underlying_symbol']}) {r['action']} {r['leverage']} "
          f"conf={r['confidence_score']:.2f} @ ${r['entry_price']:.2f} "
          f"sl={sl_str} tp={tp_str} "
          f"({r['conviction_level']}, {r['trading_type']}, {r['holding_period_hours']}h) "
          f"@ {r['recommended_at']}")

print("\n=== TRADE SNAPSHOTS FULL DETAILS ===")
cur3.execute('''
    SELECT id, trade_id, horizon_label, horizon_minutes, target_timestamp,
           observed_price, raw_return_pct, leveraged_return_pct, observed_at, source_interval
    FROM trade_snapshots
    ORDER BY trade_id, horizon_label
''')
for r in cur3.fetchall():
    print(f"  trade #{r['trade_id']} [{r['horizon_label']}] price=${r['observed_price']:.2f} "
          f"return={r['raw_return_pct']:.2f}% (lev {r['leveraged_return_pct']:.2f}%) "
          f"@ {r['observed_at']}")

print("\n=== TRADE EXECUTIONS ===")
cur3.execute('SELECT * FROM trade_executions')
for r in cur3.fetchall():
    print(f"  id={r['id']} trade_id={r['trade_id']} action={r['executed_action']} "
          f"price={r['executed_price']} @ {r['executed_at']}")

print("\n=== TRADE CLOSES ===")
cur3.execute('SELECT * FROM trade_closes')
for r in cur3.fetchall():
    print(f"  id={r['id']} trade_id={r['trade_id']} close_price={r['closed_price']} "
          f"@ {r['closed_at']}")

# Symbol frequency analysis
print("\n=== SYMBOL FREQUENCY ANALYSIS ===")
cur3.execute('''
    SELECT 
        COALESCE(underlying_symbol, symbol) as primary_symbol,
        symbol as execution_ticker,
        action,
        leverage,
        COUNT(*) as frequency,
        AVG(confidence_score) as avg_conf,
        MIN(recommended_at) as first_seen,
        MAX(recommended_at) as last_seen
    FROM trades
    GROUP BY primary_symbol, symbol, action, leverage
    ORDER BY frequency DESC
''')
for r in cur3.fetchall():
    print(f"  {r['execution_ticker']} (underlying: {r['primary_symbol']}) {r['action']} {r['leverage']}: "
          f"{r['frequency']}x, avg conf={r['avg_conf']:.2f}, "
          f"first={r['first_seen']}, last={r['last_seen']}")

# Action distribution
print("\n=== ACTION DISTRIBUTION ===")
cur3.execute('''
    SELECT 
        action,
        COUNT(*) as count,
        AVG(confidence_score) as avg_conf,
        AVG(holding_period_hours) as avg_hold
    FROM trades
    GROUP BY action
''')
for r in cur3.fetchall():
    print(f"  {r['action']}: {r['count']} trades, avg conf={r['avg_conf']:.2f}, avg hold={r['avg_hold']:.1f}h")

# Leverage distribution
print("\n=== LEVERAGE DISTRIBUTION ===")
cur3.execute('''
    SELECT 
        leverage,
        COUNT(*) as count,
        AVG(confidence_score) as avg_conf,
        GROUP_CONCAT(DISTINCT action) as actions
    FROM trades
    GROUP BY leverage
''')
for r in cur3.fetchall():
    print(f"  {r['leverage']}: {r['count']} trades, avg conf={r['avg_conf']:.2f}, actions: {r['actions']}")

# Signal type distribution
print("\n=== SIGNAL TYPE DISTRIBUTION ===")
cur3.execute('''
    SELECT 
        signal_type,
        COUNT(*) as count,
        AVG(confidence_score) as avg_conf
    FROM trades
    GROUP BY signal_type
''')
for r in cur3.fetchall():
    print(f"  {r['signal_type']}: {r['count']} trades, avg conf={r['avg_conf']:.2f}")

# Analysis results linked to trades
print("\n=== ANALYSIS RESULTS LINKED TO TRADES ===")
cur3.execute('''
    SELECT 
        ar.id as analysis_id,
        ar.timestamp as analysis_time,
        ar.signal,
        t.symbol,
        t.action,
        t.confidence_score
    FROM analysis_results ar
    JOIN trades t ON t.analysis_id = ar.id
    ORDER BY ar.timestamp
''')
for r in cur3.fetchall():
    signal = r['signal'] if isinstance(r['signal'], str) else str(r['signal'])
    print(f"  analysis #{r['analysis_id']} at {r['analysis_time']}: "
          f"symbol={r['symbol']}, action={r['action']}, conf={r['confidence_score']}, "
          f"signal={signal[:100]}...")

conn3.close()
print("\n=== COMPLETE ANALYSIS DONE ===")

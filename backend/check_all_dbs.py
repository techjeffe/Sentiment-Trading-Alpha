"""Comprehensive trade analysis across all databases."""
import sqlite3
import os
from datetime import datetime

def analyze_table(conn, table_name):
    """Get sample rows and column info for a trade-related table."""
    cur = conn.cursor()
    try:
        # Get column info
        cur.execute(f"PRAGMA table_info('{table_name}')")
        columns = [row[1] for row in cur.fetchall()]
        
        # Get count
        cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        count = cur.fetchone()[0]
        print(f"\n  Table: {table_name} ({count} rows)")
        print(f"  Columns: {', '.join(columns)}")
        
        if count > 0:
            # Get sample data (first 5 rows)
            cur.execute(f'SELECT * FROM "{table_name}" LIMIT 5')
            rows = cur.fetchall()
            for i, row in enumerate(rows):
                print(f"    Row {i+1}: {row}")
        
        if count > 5:
            print(f"    ... and {count - 5} more rows")
    except Exception as e:
        print(f"  Error reading {table_name}: {e}")

# Use absolute paths from script location
import sys
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

dbs = [
    (os.path.join(root_dir, 'trading_system.db'), 'Main DB (trading_system.db)'),
    (os.path.join(root_dir, 'decision_log.db'), 'Decision Log DB'),
]

for db_path, label in dbs:
    if os.path.exists(db_path):
        print(f"\n{'='*70}")
        print(f"=== {label}: {db_path} ===")
        print('='*70)
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute('SELECT name FROM sqlite_master WHERE type="table"')
        tables = [r[0] for r in cur.fetchall()]
        print(f"  Total tables: {len(tables)}")
        print(f"  Table list: {', '.join(tables)}")
        
        # Focus on trade-related tables
        trade_tables = [t for t in tables if any(kw in t.lower() for kw in ['trade', 'alpaca', 'order', 'paper'])]
        print(f"\n  *** Trade-related tables: {', '.join(trade_tables)} ***")
        
        for t in trade_tables:
            analyze_table(conn, t)
        
        conn.close()
    else:
        print(f"\n{db_path} NOT FOUND")

# Now do a deep dive on paper_trades and alpaca_orders
print(f"\n\n{'='*70}")
print(f"=== DEEP DIVE: Paper Trades Summary ===")
print('='*70)

db_path = os.path.join(root_dir, 'trading_system.db')
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Basic stats
    cur.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN exited_at IS NOT NULL THEN 1 ELSE 0 END) as closed,
            SUM(CASE WHEN exited_at IS NULL THEN 1 ELSE 0 END) as open,
            ROUND(AVG(CASE WHEN realized_pnl_pct IS NOT NULL THEN realized_pnl_pct END), 2) as avg_pnl_pct,
            SUM(CASE WHEN realized_pnl_pct IS NOT NULL AND realized_pnl_pct > 0 THEN 1 ELSE 0 END) as winners,
            SUM(CASE WHEN realized_pnl_pct IS NOT NULL AND realized_pnl_pct < 0 THEN 1 ELSE 0 END) as losers
        FROM paper_trades
    """)
    stats = cur.fetchone()
    print(f"  Total trades: {stats[0]}")
    print(f"  Closed: {stats[1]}, Open: {stats[2]}")
    print(f"  Average PnL%: {stats[3]}%")
    print(f"  Winners: {stats[4]}, Losers: {stats[5]}")
    
    # By symbol
    print("\n  By underlying symbol:")
    cur.execute("""
        SELECT underlying, 
               COUNT(*) as total,
               SUM(CASE WHEN exited_at IS NOT NULL THEN 1 ELSE 0 END) as closed,
               ROUND(AVG(CASE WHEN realized_pnl_pct IS NOT NULL THEN realized_pnl_pct END), 2) as avg_pnl_pct,
               SUM(CASE WHEN realized_pnl_pct IS NOT NULL AND realized_pnl_pct > 0 THEN 1 ELSE 0 END) as winners,
               SUM(CASE WHEN realized_pnl_pct IS NOT NULL AND realized_pnl_pct < 0 THEN 1 ELSE 0 END) as losers,
               ROUND(SUM(CASE WHEN realized_pnl_pct IS NOT NULL THEN realized_pnl_pct END), 2) as total_pnl_pct
        FROM paper_trades
        WHERE exited_at IS NOT NULL
        GROUP BY underlying
        ORDER BY total DESC
    """)
    for row in cur.fetchall():
        print(f"    {row[0]}: {row[1]} total, {row[2]} closed, avg={row[3]}%, win={row[4]}, lose={row[5]}, total_pnl={row[6]}%")
    
    # By signal type
    print("\n  By signal type:")
    cur.execute("""
        SELECT signal_type,
               COUNT(*) as total,
               ROUND(AVG(CASE WHEN realized_pnl_pct IS NOT NULL THEN realized_pnl_pct END), 2) as avg_pnl_pct,
               SUM(CASE WHEN realized_pnl_pct IS NOT NULL AND realized_pnl_pct > 0 THEN 1 ELSE 0 END) as winners,
               SUM(CASE WHEN realized_pnl_pct IS NOT NULL AND realized_pnl_pct < 0 THEN 1 ELSE 0 END) as losers
        FROM paper_trades
        WHERE exited_at IS NOT NULL
        GROUP BY signal_type
    """)
    for row in cur.fetchall():
        print(f"    {row[0]}: {row[1]} total, avg={row[2]}%, win={row[3]}, lose={row[4]}")
    
    # By conviction level
    print("\n  By conviction level:")
    cur.execute("""
        SELECT conviction_level,
               COUNT(*) as total,
               ROUND(AVG(CASE WHEN realized_pnl_pct IS NOT NULL THEN realized_pnl_pct END), 2) as avg_pnl_pct
        FROM paper_trades
        WHERE exited_at IS NOT NULL
        GROUP BY conviction_level
    """)
    for row in cur.fetchall():
        print(f"    {row[0]}: {row[1]} total, avg={row[2]}%")
    
    conn.close()

print(f"\n\n{'='*70}")
print(f"=== DEEP DIVE: Alpaca Orders Summary ===")
print('='*70)

db_path = os.path.join(root_dir, 'trading_system.db')
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Check if alpaca_orders table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alpaca_orders'")
    if not cur.fetchone():
        print("  alpaca_orders table does NOT exist in this database.")
    else:
        cur.execute("SELECT COUNT(*) FROM alpaca_orders")
        count = cur.fetchone()[0]
        print(f"  Total alpaca orders: {count}")
        
        if count > 0:
            # By status
            cur.execute("""
                SELECT status, COUNT(*) as cnt, COUNT(DISTINCT paper_trade_id) as trades
                FROM alpaca_orders
                GROUP BY status
            """)
            print("\n  By status:")
            for row in cur.fetchall():
                print(f"    {row[0]}: {row[1]} orders ({row[2]} trades)")
            
            # By trading mode
            cur.execute("""
                SELECT trading_mode, COUNT(*) as cnt, SUM(CASE WHEN status='filled' THEN 1 ELSE 0 END) as filled
                FROM alpaca_orders
                GROUP BY trading_mode
            """)
            print("\n  By trading mode:")
            for row in cur.fetchall():
                print(f"    {row[0]}: {row[1]} orders, {row[2]} filled")
            
            # By symbol
            cur.execute("""
                SELECT symbol, side, status, filled_qty, filled_avg_price, error_message
                FROM alpaca_orders
                ORDER BY created_at DESC
                LIMIT 20
            """)
            print("\n  Recent orders:")
            for row in cur.fetchall():
                print(f"    {row[2]} | {row[1]} | {row[0]} | qty={row[3]} | price={row[4]} | err={row[5]}")
    
    conn.close()

print(f"\n\n{'='*70}")
print(f"=== DEEP DIVE: Decision Log Trades ===")
print('='*70)

db_path = os.path.join(root_dir, 'decision_log.db')
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%trade%'")
    tables = [r[0] for r in cur.fetchall()]
    print(f"  Trade-related tables: {', '.join(tables)}")
    
    if 'decision_log_trade' in tables:
        cur.execute("SELECT COUNT(*) FROM decision_log_trade")
        count = cur.fetchone()[0]
        print(f"\n  decision_log_trade: {count} trades")
        
        # Summary stats
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN closed = 1 THEN 1 ELSE 0 END) as closed,
                ROUND(AVG(CASE WHEN closed = 1 AND realized_pnl IS NOT NULL THEN realized_pnl END), 2) as avg_pnl,
                SUM(CASE WHEN closed = 1 AND realized_pnl > 0 THEN 1 ELSE 0 END) as winners,
                SUM(CASE WHEN closed = 1 AND realized_pnl < 0 THEN 1 ELSE 0 END) as losers
            FROM decision_log_trade
        """)
        stats = cur.fetchone()
        print(f"  Closed: {stats[1]}, Avg PnL: ${stats[2]}, Winners: {stats[3]}, Losers: {stats[4]}")
        
        # By symbol
        cur.execute("""
            SELECT symbol, direction,
                   COUNT(*) as total,
                   SUM(CASE WHEN closed = 1 THEN 1 ELSE 0 END) as closed,
                   ROUND(AVG(CASE WHEN closed = 1 AND realized_pnl IS NOT NULL THEN realized_pnl END), 2) as avg_pnl,
                   SUM(CASE WHEN closed = 1 AND realized_pnl > 0 THEN 1 ELSE 0 END) as winners,
                   SUM(CASE WHEN closed = 1 AND realized_pnl < 0 THEN 1 ELSE 0 END) as losers,
                   ROUND(SUM(CASE WHEN closed = 1 AND realized_pnl IS NOT NULL THEN realized_pnl END), 2) as total_pnl
            FROM decision_log_trade
            GROUP BY symbol, direction
            ORDER BY total DESC
        """)
        print("\n  By symbol/direction:")
        for row in cur.fetchall():
            print(f"    {row[0]} ({row[1]}): {row[2]} total, {row[3]} closed, avg=${row[4]}, win={row[5]}, lose={row[6]}, total=${row[7]}")
    
    conn.close()

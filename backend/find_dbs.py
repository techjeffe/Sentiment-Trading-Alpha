import sqlite3

for db in ['decision_log.db', 'trading_system.db']:
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute('SELECT name FROM sqlite_master WHERE type="table"')
    tables = [r[0] for r in cur.fetchall()]
    print(f'{db}: {tables}')
    for t in tables:
        cur.execute(f'SELECT COUNT(*) FROM {t}')
        print(f'  {t}: {cur.fetchone()[0]} rows')
    conn.close()
    print()
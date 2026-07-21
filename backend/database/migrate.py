"""
Database migration script. SQLite only — run to update the schema.
Can be run from any directory: python backend/database/migrate.py
"""

import os
import sys
from pathlib import Path

# Ensure the backend/ directory is on sys.path so imports work regardless of cwd.
_backend_dir = str(Path(__file__).resolve().parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from database.engine import DEFAULT_DATABASE_URL

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def migrate():
    """Apply all pending schema migrations."""
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

    with engine.connect() as conn:
        # ── app_config columns ──────────────────────────────────────────────
        existing_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(app_config)")).fetchall()]
        for column_name, column_type, default_value in [
            ("data_ingestion_interval_seconds", "INTEGER", "900"),
            ("snapshot_retention_limit", "INTEGER", "12"),
            ("custom_symbols", "JSON", "'[]'"),
            ("display_timezone", "VARCHAR(64)", "''"),
            ("symbol_company_aliases", "JSON", "'{}'"),
            ("symbol_proxy_terms", "JSON", "'{}'"),
            ("symbol_proxy_terms_generated_at", "JSON", "'{}'"),
            ("symbol_edgar_ciks", "JSON", "'{}'"),
            ("enabled_rss_feeds", "JSON", "'[]'"),
            ("custom_rss_feeds", "JSON", "'[]'"),
            ("custom_rss_feed_labels", "JSON", "'{}'"),
            ("rss_article_detail_mode", "VARCHAR(20)", "'normal'"),
            ("rss_article_limits", "JSON", "'{\"light\":5,\"normal\":10,\"detailed\":20}'"),
            ("extraction_model", "VARCHAR(128)", "''"),
            ("reasoning_model", "VARCHAR(128)", "''"),
            ("ollama_parallel_slots", "INTEGER", "1"),
            ("red_team_enabled", "BOOLEAN", "1"),
            ("inference_backend", "VARCHAR(16)", "'ollama'"),
            ("openai_base_url", "VARCHAR(256)", "'https://api.openai.com/v1'"),
            ("openai_model", "VARCHAR(128)", "'gpt-4o-mini'"),
            ("api_mode", "VARCHAR(16)", "'local'"),
            ("cloud_provider", "VARCHAR(32)", "'openai'"),
            ("local_provider", "VARCHAR(32)", "'ollama'"),
            ("ollama_url", "VARCHAR(256)", "'http://localhost:11434/api/generate'"),
            ("vllm_url", "VARCHAR(256)", "'http://localhost:8000'"),
            ("risk_profile", "VARCHAR(20)", "'standard'"),
            ("risk_policy", "JSON", "'{}'"),
            ("web_research_enabled", "BOOLEAN", "0"),
            ("allow_extended_hours_trading", "BOOLEAN", "1"),
            ("remote_snapshot_enabled", "BOOLEAN", "0"),
            ("telegram_remote_control_enabled", "BOOLEAN", "0"),
            ("telegram_remote_control_banner_active", "BOOLEAN", "0"),
            ("telegram_remote_control_banner_message", "TEXT", "NULL"),
            ("telegram_remote_control_banner_updated_at", "DATETIME", "NULL"),
            ("remote_snapshot_mode", "VARCHAR(20)", "'telegram'"),
            ("remote_snapshot_min_pnl_change_usd", "REAL", "5.0"),
            ("remote_snapshot_heartbeat_minutes", "INTEGER", "360"),
            ("remote_snapshot_interval_minutes", "INTEGER", "360"),
            ("remote_snapshot_send_on_position_change", "BOOLEAN", "1"),
            ("remote_snapshot_include_closed_trades", "BOOLEAN", "0"),
            ("remote_snapshot_max_recommendations", "INTEGER", "4"),
            ("last_remote_snapshot_sent_at", "DATETIME", "NULL"),
            ("last_remote_snapshot_request_id", "VARCHAR(36)", "NULL"),
            ("last_remote_snapshot_net_pnl", "REAL", "NULL"),
            ("last_remote_snapshot_recommendation_fingerprint", "VARCHAR(255)", "NULL"),
            ("analysis_lock_request_id", "VARCHAR(36)", "NULL"),
            ("analysis_lock_acquired_at", "DATETIME", "NULL"),
            ("analysis_lock_expires_at", "DATETIME", "NULL"),
        ]:
            if column_name not in existing_cols:
                print(f"Adding {column_name} to app_config...")
                nullable = default_value == "NULL"
                null_sql = "" if nullable else " NOT NULL"
                default_sql = "" if nullable else f" DEFAULT {default_value}"
                conn.exec_driver_sql(f"ALTER TABLE app_config ADD COLUMN {column_name} {column_type}{null_sql}{default_sql}")
                conn.commit()

        # ── app_config: nullable trading-logic override columns ─────────────
        for column_name, column_type in [
            ("vol_sizing_portfolio_cap_usd", "REAL"),
            ("paper_trade_amount", "REAL"),
            ("entry_threshold", "REAL"),
            ("stop_loss_pct", "REAL"),
            ("take_profit_pct", "REAL"),
            ("materiality_min_posts_delta", "INTEGER"),
            ("materiality_min_sentiment_delta", "REAL"),
            ("reentry_cooldown_minutes", "INTEGER"),
            ("min_same_day_exit_edge_pct", "REAL"),
            ("continuous_entry_enabled", "BOOLEAN"),
            ("regime_adaptation_enabled", "BOOLEAN"),
            ("hold_decay_enabled", "BOOLEAN"),
            ("accumulate_on_confirmation_enabled", "BOOLEAN"),
            ("accumulate_max_multiplier", "REAL"),
            ("edgar_filings_enabled", "BOOLEAN"),
            ("edgar_filings_poll_interval_minutes", "INTEGER"),
            ("edgar_filings_tracked_form_types", "JSON"),
            ("edgar_filings_material_8k_items", "JSON"),
        ]:
            if column_name not in existing_cols:
                print(f"Adding {column_name} to app_config...")
                conn.exec_driver_sql(f"ALTER TABLE app_config ADD COLUMN {column_name} {column_type}")
                conn.commit()

        # ── app_config: boolean trading-logic flags ──────────────────────────
        for column_name, column_type, default_value in [
            ("hold_overnight", "BOOLEAN", "0"),
            ("trail_on_window_expiry", "BOOLEAN", "1"),
        ]:
            if column_name not in existing_cols:
                print(f"Adding {column_name} to app_config...")
                conn.exec_driver_sql(f"ALTER TABLE app_config ADD COLUMN {column_name} {column_type} NOT NULL DEFAULT {default_value}")
                conn.commit()

        # ── app_config: Alpaca brokerage execution columns ───────────────────
        existing_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(app_config)")).fetchall()]
        for column_name, column_type, default_value in [
            ("alpaca_execution_mode",         "VARCHAR(10)", "'off'"),
            ("alpaca_live_trading_enabled",   "BOOLEAN", "0"),
            ("alpaca_allow_short_selling",    "BOOLEAN", "0"),
            ("alpaca_fixed_order_size",       "BOOLEAN", "0"),
            ("alpaca_order_type",             "VARCHAR(20)", "'market'"),
            ("alpaca_limit_slippage_pct",     "REAL", "0.002"),
            ("alpaca_high_conviction_override_enabled", "BOOLEAN", "0"),
        ]:
            if column_name not in existing_cols:
                print(f"Adding {column_name} to app_config...")
                conn.exec_driver_sql(f"ALTER TABLE app_config ADD COLUMN {column_name} {column_type} NOT NULL DEFAULT {default_value}")
                conn.commit()
        for column_name, column_type in [
            ("alpaca_paper_trade_amount_usd", "REAL"),
            ("alpaca_live_trade_amount_usd",  "REAL"),
            ("alpaca_max_position_usd",       "REAL"),
            ("alpaca_max_total_exposure_usd", "REAL"),
            ("alpaca_daily_loss_limit_usd",   "REAL"),
            ("alpaca_max_consecutive_losses", "INTEGER"),
            ("circuit_breaker_fired_at",      "TIMESTAMP"),
            ("circuit_breaker_reason",        "VARCHAR(255)"),
            ("alpaca_pre_stop_mode",          "VARCHAR(10)"),
            ("alpaca_min_close_notional",     "REAL"),
        ]:
            if column_name not in existing_cols:
                print(f"Adding {column_name} to app_config...")
                conn.exec_driver_sql(f"ALTER TABLE app_config ADD COLUMN {column_name} {column_type}")
                conn.commit()

        # ── trades table ─────────────────────────────────────────────────────
        existing_trades_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(trades)")).fetchall()]
        for column_name, column_type, default_value in [
            ("underlying_symbol", "VARCHAR(10)", "NULL"),
            ("conviction_level", "VARCHAR(20)", "'MEDIUM'"),
            ("holding_period_hours", "INTEGER", "4"),
            ("trading_type", "VARCHAR(20)", "'SWING'"),
            ("holding_window_until", "DATETIME", "NULL"),
        ]:
            if column_name not in existing_trades_cols:
                print(f"Adding {column_name} to trades...")
                conn.execute(text(f"ALTER TABLE trades ADD COLUMN {column_name} {column_type} DEFAULT {default_value}"))
                conn.commit()

        existing_indexes = [row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'")).fetchall()]
        for index_name, table_name, column_name in [
            ("ix_trades_underlying_symbol", "trades", "underlying_symbol"),
            ("ix_trades_holding_window_until", "trades", "holding_window_until"),
            ("ix_trades_conviction_level", "trades", "conviction_level"),
            ("ix_app_config_analysis_lock_expires_at", "app_config", "analysis_lock_expires_at"),
        ]:
            if index_name not in existing_indexes:
                print(f"Creating index {index_name}...")
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({column_name})"))
                conn.commit()

        # ── price_history table ───────────────────────────────────────────────
        tables = [row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()]
        if "price_history" not in tables:
            print("Creating price_history table...")
            conn.execute(text("""
                CREATE TABLE price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol VARCHAR(10) NOT NULL,
                    date VARCHAR(10) NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    adj_close REAL,
                    volume REAL,
                    source VARCHAR(20) NOT NULL DEFAULT 'yfinance',
                    fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, date)
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_price_history_symbol_date ON price_history (symbol, date)"))
            conn.commit()
            print("price_history table created.")

        # ── scraped_articles table ────────────────────────────────────────────
        if "scraped_articles" not in tables:
            print("Creating scraped_articles table...")
            conn.execute(text("""
                CREATE TABLE scraped_articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source VARCHAR(100) NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    full_content TEXT NOT NULL DEFAULT '',
                    published_at DATETIME,
                    discovered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    processed BOOLEAN NOT NULL DEFAULT 0,
                    processed_at DATETIME,
                    fast_lane_triggered BOOLEAN NOT NULL DEFAULT 0,
                    content_hash VARCHAR(64)
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_scraped_articles_processed ON scraped_articles (processed)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_scraped_articles_published_at ON scraped_articles (published_at)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_scraped_articles_discovered_at ON scraped_articles (discovered_at)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_scraped_articles_content_hash ON scraped_articles (content_hash)"))
            conn.commit()
            print("scraped_articles table created.")
        else:
            # Add new columns to existing scraped_articles table
            existing_sa_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(scraped_articles)")).fetchall()]
            for column_name, column_type in [
                ("processed_at", "DATETIME"),
                ("content_hash", "VARCHAR(64)"),
            ]:
                if column_name not in existing_sa_cols:
                    print(f"Adding {column_name} to scraped_articles...")
                    conn.exec_driver_sql(f"ALTER TABLE scraped_articles ADD COLUMN {column_name} {column_type}")
                    conn.commit()
            # Add content_hash index if missing
            existing_indexes = [row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'")).fetchall()]
            if "ix_scraped_articles_content_hash" not in existing_indexes:
                print("Creating index ix_scraped_articles_content_hash...")
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_scraped_articles_content_hash ON scraped_articles (content_hash)"))
                conn.commit()

        # ── paper_trades table ────────────────────────────────────────────────
        if "paper_trades" not in tables:
            print("Creating paper_trades table...")
            conn.execute(text("""
                CREATE TABLE paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    underlying VARCHAR(10) NOT NULL,
                    execution_ticker VARCHAR(10) NOT NULL,
                    signal_type VARCHAR(10) NOT NULL,
                    leverage VARCHAR(10) NOT NULL DEFAULT '1x',
                    market_session VARCHAR(20),
                    amount REAL NOT NULL DEFAULT 100.0,
                    shares REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    entered_at DATETIME NOT NULL,
                    exited_at DATETIME,
                    realized_pnl REAL,
                    realized_pnl_pct REAL,
                    analysis_request_id VARCHAR(64),
                    conviction_level VARCHAR(10),
                    trading_type VARCHAR(20),
                    holding_period_hours INTEGER,
                    holding_window_until DATETIME,
                    close_reason VARCHAR(40),
                    trailing_stop_price REAL,
                    best_price_seen REAL
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_trades_underlying ON paper_trades (underlying)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_trades_entered_at ON paper_trades (entered_at)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_paper_trades_exited_at ON paper_trades (exited_at)"))
            conn.commit()
            print("paper_trades table created.")
        else:
            existing_pt_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(paper_trades)")).fetchall()]
            for column_name, column_type in [
                ("conviction_level",     "VARCHAR(10)"),
                ("trading_type",         "VARCHAR(20)"),
                ("holding_period_hours", "INTEGER"),
                ("holding_window_until", "DATETIME"),
                ("close_reason",         "VARCHAR(40)"),
                ("trailing_stop_price",  "REAL"),
                ("best_price_seen",      "REAL"),
                ("original_amount",      "REAL"),
                ("ramp_stage",           "VARCHAR(20)"),
                ("ramp_promotion_count", "INTEGER"),
            ]:
                if column_name not in existing_pt_cols:
                    print(f"Adding {column_name} to paper_trades...")
                    conn.exec_driver_sql(f"ALTER TABLE paper_trades ADD COLUMN {column_name} {column_type}")
                    conn.commit()

        # ── trade_closes table ────────────────────────────────────────────────
        if "trade_closes" not in tables:
            print("Creating trade_closes table...")
            conn.execute(text("""
                CREATE TABLE trade_closes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER NOT NULL UNIQUE REFERENCES trades(id),
                    closed_price REAL NOT NULL,
                    closed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    notes TEXT
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trade_closes_trade_id ON trade_closes (trade_id)"))
            conn.commit()
            print("trade_closes table created.")

        # ── alpaca_orders table ───────────────────────────────────────────────
        if "alpaca_orders" not in tables:
            print("Creating alpaca_orders table...")
            conn.execute(text("""
                CREATE TABLE alpaca_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    paper_trade_id INTEGER REFERENCES paper_trades(id),
                    alpaca_order_id VARCHAR(64),
                    client_order_id VARCHAR(128) UNIQUE,
                    symbol VARCHAR(20) NOT NULL,
                    side VARCHAR(10) NOT NULL,
                    notional REAL,
                    qty REAL,
                    order_type VARCHAR(20) NOT NULL DEFAULT 'market',
                    time_in_force VARCHAR(10) NOT NULL DEFAULT 'day',
                    limit_price REAL,
                    extended_hours BOOLEAN NOT NULL DEFAULT 0,
                    status VARCHAR(30),
                    filled_qty REAL,
                    filled_avg_price REAL,
                    submitted_at DATETIME,
                    filled_at DATETIME,
                    trading_mode VARCHAR(10) NOT NULL DEFAULT 'paper',
                    raw_response JSON,
                    error_message TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    is_orphan BOOLEAN NOT NULL DEFAULT 0,
                    orphan_acknowledged BOOLEAN NOT NULL DEFAULT 0
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_alpaca_orders_paper_trade_id ON alpaca_orders (paper_trade_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_alpaca_orders_alpaca_order_id ON alpaca_orders (alpaca_order_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_alpaca_orders_created_at ON alpaca_orders (created_at)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_alpaca_orders_trading_mode ON alpaca_orders (trading_mode)"))
            conn.commit()
            print("alpaca_orders table created.")
        else:
            existing_ao_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(alpaca_orders)")).fetchall()]
            for column_name, column_type, default_value in [
                ("is_orphan",           "BOOLEAN", "0"),
                ("orphan_acknowledged", "BOOLEAN", "0"),
            ]:
                if column_name not in existing_ao_cols:
                    print(f"Adding {column_name} to alpaca_orders...")
                    conn.exec_driver_sql(f"ALTER TABLE alpaca_orders ADD COLUMN {column_name} {column_type} NOT NULL DEFAULT {default_value}")
                    conn.commit()

        # ── alpaca_dispatch_errors table ────────────────────────────────────
        if "alpaca_dispatch_errors" not in tables:
            print("Creating alpaca_dispatch_errors table...")
            conn.execute(text("""
                CREATE TABLE alpaca_dispatch_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol VARCHAR(20) NOT NULL,
                    underlying VARCHAR(20),
                    error_type VARCHAR(50) NOT NULL,
                    error_message TEXT,
                    paper_trade_id INTEGER,
                    trading_mode VARCHAR(10) NOT NULL DEFAULT 'live',
                    acknowledged BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_alpaca_dispatch_errors_acknowledged ON alpaca_dispatch_errors (acknowledged)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_alpaca_dispatch_errors_created_at ON alpaca_dispatch_errors (created_at)"))
            conn.commit()
            print("alpaca_dispatch_errors table created.")

        # ── sec_filings table ────────────────────────────────────────────────
        if "sec_filings" not in tables:
            print("Creating sec_filings table...")
            conn.execute(text("""
                CREATE TABLE sec_filings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol VARCHAR(10) NOT NULL,
                    cik VARCHAR(10) NOT NULL,
                    accession_number VARCHAR(24) NOT NULL UNIQUE,
                    form_type VARCHAR(20) NOT NULL,
                    filing_date DATETIME NOT NULL,
                    report_date DATETIME,
                    items VARCHAR(64),
                    primary_document_url TEXT NOT NULL,
                    raw_text TEXT,
                    llm_summary TEXT,
                    processed BOOLEAN NOT NULL DEFAULT 0,
                    discovered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    processed_at DATETIME
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sec_filings_symbol ON sec_filings (symbol)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sec_filings_processed ON sec_filings (processed)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sec_filings_filing_date ON sec_filings (filing_date)"))
            conn.commit()
            print("sec_filings table created.")

    # ── Decision Log tables ──────────────────────────────────────────────
    # Created via metadata.create_all on the decision_log engine during
    # backend startup.  Column additions still need explicit ALTER TABLE
    # because create_all is a no-op on existing tables.
    try:
        from database.engine import DECISION_LOG_PATH
        dl_engine = create_engine(f"sqlite:///{DECISION_LOG_PATH.as_posix()}", connect_args={"check_same_thread": False})
        with dl_engine.connect() as dl_conn:
            dl_tables = [row[0] for row in dl_conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()]
            if "decision_log_symbol" in dl_tables:
                dl_sym_cols = [row[1] for row in dl_conn.execute(text("PRAGMA table_info(decision_log_symbol)")).fetchall()]
                for col_name, col_type in [
                    ("event_type", "VARCHAR(32)"),
                    ("keyword_attribution", "JSON"),
                ]:
                    if col_name not in dl_sym_cols:
                        print(f"Adding {col_name} to decision_log_symbol...")
                        dl_conn.exec_driver_sql(f"ALTER TABLE decision_log_symbol ADD COLUMN {col_name} {col_type}")
                        dl_conn.commit()
    except Exception as dl_exc:
        print(f"Decision log migration warning: {dl_exc}")


if __name__ == "__main__":
    try:
        migrate()
        print("\nMigration completed successfully!")
    except Exception as e:
        print(f"\nMigration failed: {e}")
        raise

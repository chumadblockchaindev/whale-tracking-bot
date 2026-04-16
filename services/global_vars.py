import pandas as pd

CSV_FILE = "data/tokens.csv"
TRADE_CSV_FILE = "data/trades.csv"

COLUMNS = [
    'mint', 'market_cap', 'dev_wallet', 'created_at',
    'whale_wallet', 'whale_wallet_count', 'whale_roi_pct', 'whale_age_hours'
]

TRADE_COLUMNS = [
    'token_mint', 'side', 'quantity', 'entry_price_sol',
    'exit_price_sol', 'profit_sol', 'roi_pct', 'reason', 'timestamp'
]

DEFAULT_ROW = {
    'mint': '',
    'market_cap': 0.0,
    'dev_wallet': '',
    'created_at': '',
    'whale_wallet': '',
    'whale_wallet_count': 0,
    'whale_roi_pct': 0.0,
    'whale_age_hours': 0.0
}

TRADE_DEFAULT_ROW = {
    'token_mint': '',
    'side': '',
    'quantity': 0.0,
    'entry_price_sol': 0.0,
    'exit_price_sol': 0.0,
    'profit_sol': 0.0,
    'roi_pct': 0.0,
    'reason': '',
    'timestamp': 0
}

COMMON_COLUMN_DTYPES = {
    'mint': str,
    'market_cap': float,
    'dev_wallet': str,
    'created_at': float,  # Store as Unix timestamp
    'whale_wallet': str,
    'whale_wallet_count': int,
    'whale_roi_pct': float,
    'whale_age_hours': float
}

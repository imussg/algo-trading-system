from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.common.exceptions import APIError
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from datetime import timedelta, datetime, time as dtime, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import os
import schedule
import time

# --- setup / clients ---
load_dotenv()
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

client = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

symbols = ["AAPL", "MSFT", "GOOGL", "JPM", "XOM"]

TIMEFRAME_MAP = {
    "1Min": TimeFrame(1, TimeFrameUnit.Minute),
    "5Min": TimeFrame(5, TimeFrameUnit.Minute),
    "1Hour": TimeFrame(1, TimeFrameUnit.Hour)
}
INTERVAL_TIMEDELTA = {
    "1Min": timedelta(minutes=1),
    "5Min": timedelta(minutes=5),
    "1Hour": timedelta(hours=1)
}

# --- function definitions ---
def get_live_bars(symbol, interval="5Min", lookback=5):
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TIMEFRAME_MAP[interval],
        start=datetime.now() - timedelta(days=lookback)
    )
    bars = data_client.get_stock_bars(request)
    df = bars.df

    # verify that the bar loaded is not a new one that has too few data points to run the 5 minute metrics on
    if not df.empty:
        last_bar_start = df.index.get_level_values('timestamp')[-1]
        if last_bar_start + INTERVAL_TIMEDELTA[interval] > datetime.now(timezone.utc):
            df = df.iloc[:-1]
    return df


def calculate_macd(df, fast=12, slow=26, signal=9):
    # calculate fast and slow EMAs
    df['ema_fast'] = df['close'].ewm(span=fast, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=slow, adjust=False).mean()

    # calculate MACD line (fast minus slow)
    df['macd'] = df['ema_fast'] - df['ema_slow']

    # calculate signal line (EMA of MACD line)
    df['signal'] = df['macd'].ewm(span=signal, adjust=False).mean()

    # calculate histogram (MACD minus signal)
    df['hist'] = df['macd'] - df['signal']

    return df


def run_signals(df, symbol, qty=1, actionable=False):
    latest_hist = df['hist'].iloc[-1]
    previous_hist = df['hist'].iloc[-2]

    if previous_hist > 0 and latest_hist <= 0:
        action = 'SELL'
    elif previous_hist < 0 and latest_hist >= 0:
        action = 'BUY'
    else:
        action = 'HOLD'

    try:
        position = client.get_open_position(symbol)
        print(f"Currently holding {position.qty} shares of {symbol}")
        qty_held = int(position.qty)
    except APIError:
        print(f"No open position in {symbol}")
        qty_held = 0

    if action == 'SELL' and qty_held > 0:
        final_action = 'SELL'
    elif action == 'BUY' and qty_held == 0:
        final_action = 'BUY'
    else:
        final_action = 'HOLD'

    if final_action in ['BUY', 'SELL']:
        if actionable:
            side = OrderSide.BUY if final_action == 'BUY' else OrderSide.SELL
            order_data = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY
            )
            client.submit_order(order_data=order_data)
            print(f"Submitted {final_action} order for {qty} share(s) of {symbol}")
        else:
            print(f"[DRY RUN] Would {final_action} {qty} share(s) of {symbol}")

    return final_action


def is_market_open():
    now = datetime.now(ZoneInfo("America/New_York"))
    open_time = dtime(9, 30)
    close_time = dtime(16, 0)
    if now.weekday() < 5 and (open_time <= now.time() <= close_time):
        return True
    return False


def job():
    if not is_market_open():
        return
    print(f"\n --- {datetime.now()} ---")
    for symbol in symbols:
        df = get_live_bars(symbol)
        df = calculate_macd(df)
        print(run_signals(df, symbol, actionable=True))


# --- entry point ---
if __name__ == "__main__":

    now = datetime.now(ZoneInfo("America/New_York"))
    
    # how many minutes away are we from when the whole 5 minute ticker data is published
    minutes = now.minute % 5

    # delay in seconds to account for possible time needed to calculate and publish
    delay = 1
    
    time.sleep((5 - minutes) * 60 + delay - now.second)
    schedule.every(5).minutes.do(job)
    while True:
        schedule.run_pending()
        time.sleep(1)

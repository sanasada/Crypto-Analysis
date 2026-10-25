import os, time, threading, json, requests
from datetime import datetime, timezone
from typing import Literal
import ccxt
import pandas as pd
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

APP_NAME = "Realtime Market Watcher — Signals Only"
STARTING_CAPITAL_PKR = float(os.getenv("STARTING_CAPITAL_PKR", "100000"))
RISK_PCT = min(float(os.getenv("RISK_PER_TRADE_PCT", "2")), 2.0)
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "3"))
MAX_LEVERAGE = min(float(os.getenv("MAX_LEVERAGE", "10")), 10.0)
SCAN_INTERVAL_SECONDS = max(int(os.getenv("SCAN_INTERVAL_SECONDS", "300")), 60)
WATCHLIST = [x.strip() for x in os.getenv("CRYPTO_WATCHLIST", "BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT").split(",") if x.strip()]
STOCK_WATCHLIST = [x.strip().upper() for x in os.getenv("STOCK_WATCHLIST", "AAPL,MSFT,NVDA,TSLA").split(",") if x.strip()]
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
REAL_ORDERS_ENABLED = False  # Hard-disabled: this service has no order-placement route.

app = FastAPI(title=APP_NAME, version="0.2.0")
exchange = ccxt.okx({"enableRateLimit": True})
state = {"running": False, "last_scan": None, "last_error": None, "signals": [], "news": [], "sent_keys": []}
lock = threading.Lock()
stop_event = threading.Event()


def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def technical_signal(df, symbol, market, price_scale=1.0):
    df = df.dropna().copy()
    if len(df) < 35:
        return None
    close = df["close"] if "close" in df else df["Close"]
    high = df["high"] if "high" in df else df["High"]
    low = df["low"] if "low" in df else df["Low"]
    volume = df["volume"] if "volume" in df else df["Volume"]
    fast, slow = ema(close, 9), ema(close, 21)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean().replace(0, 1e-9)
    rsi = 100 - (100 / (1 + gain / loss))
    tr = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    vol_avg = volume.rolling(20).mean()
    price = float(close.iloc[-1])
    rsi_now, atr_now = float(rsi.iloc[-1]), float(atr.iloc[-1])
    direction = "WAIT"
    reasons = []
    if fast.iloc[-1] > slow.iloc[-1] and 50 <= rsi_now <= 68:
        direction = "LONG"
        reasons = ["EMA9 > EMA21", f"RSI14={rsi_now:.1f} supports bullish momentum"]
    elif fast.iloc[-1] < slow.iloc[-1] and 32 <= rsi_now <= 50:
        direction = "SHORT"
        reasons = ["EMA9 < EMA21", f"RSI14={rsi_now:.1f} supports bearish momentum"]
    if pd.notna(vol_avg.iloc[-1]) and vol_avg.iloc[-1] > 0:
        vr = float(volume.iloc[-1] / vol_avg.iloc[-1])
        reasons.append(f"Volume/current 20-candle average={vr:.2f}x")
    else:
        vr = None
    if direction == "WAIT" or not (atr_now > 0):
        return {"market": market, "symbol": symbol, "direction": "WAIT", "entry": round(price, 6),
                "stop_loss": None, "take_profit": None, "rsi14": round(rsi_now, 2),
                "reasons": reasons or ["Indicators not aligned"], "timestamp": datetime.now(timezone.utc).isoformat()}
    dist = max(atr_now * 1.5, price * 0.002)
    sl = price - dist if direction == "LONG" else price + dist
    tp = price + 2*dist if direction == "LONG" else price - 2*dist
    risk_pkr = STARTING_CAPITAL_PKR * RISK_PCT / 100
    # Price in native quote currency; PKR conversion, contract sizing and fees are not applied.
    units_est = risk_pkr / dist
    return {"market": market, "symbol": symbol, "direction": direction, "entry": round(price, 6),
            "stop_loss": round(sl, 6), "take_profit": round(tp, 6), "risk_budget_pkr": round(risk_pkr, 2),
            "position_units_estimate_not_for_orders": round(units_est, 8),
            "rsi14": round(rsi_now, 2), "atr14": round(atr_now, 6), "volume_ratio": round(vr, 2) if vr is not None else None,
            "risk_reward_target": "1:2", "max_leverage_cap": MAX_LEVERAGE,
            "reasons": reasons, "timestamp": datetime.now(timezone.utc).isoformat(),
            "warning": "Signal only, not a command to trade. No orders are placed. Verify fees, FX, liquidity, contract sizing and news context."}


def telegram_send(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "disable_web_page_preview": True}, timeout=15)
    r.raise_for_status()
    return True


def news_fetch():
    # NewsAPI is optional. Without key, no news is fabricated.
    if not NEWS_API_KEY:
        return {"configured": False, "items": [], "note": "Set NEWS_API_KEY to enable headlines. Headlines are context, not trade instructions."}
    try:
        url = "https://newsapi.org/v2/everything"
        params = {"q": '(crypto OR bitcoin OR ethereum OR Federal Reserve OR inflation OR sanctions OR tariffs OR election OR war OR regulation)',
                  "language": "en", "sortBy": "publishedAt", "pageSize": 20, "apiKey": NEWS_API_KEY}
        data = requests.get(url, params=params, timeout=20).json()
        articles = []
        for a in data.get("articles", []):
            articles.append({"title": a.get("title"), "source": (a.get("source") or {}).get("name"),
                             "published_at": a.get("publishedAt"), "url": a.get("url"),
                             "description": a.get("description")})
        return {"configured": True, "items": articles, "note": "Headline monitoring only. Verify important political/geopolitical claims across reliable sources."}
    except Exception as e:
        return {"configured": True, "items": [], "error": str(e)}


def scan_once():
    found = []
    for sym in WATCHLIST:
        try:
            candles = exchange.fetch_ohlcv(sym, timeframe="1h", limit=150)
            df = pd.DataFrame(candles, columns=["timestamp","open","high","low","close","volume"])
            sig = technical_signal(df, sym, "crypto")
            if sig and sig["direction"] != "WAIT":
                found.append(sig)
        except Exception as e:
            state["last_error"] = f"Crypto {sym}: {e}"
    for sym in STOCK_WATCHLIST:
        try:
            hist = yf.Ticker(sym).history(period="1y", interval="1d", auto_adjust=False)
            if hist.empty: continue
            df = hist.rename(columns={"Open":"open","High":"high","Low":"low","Close":"close","Volume":"volume"})
            sig = technical_signal(df, sym, "stock")
            if sig and sig["direction"] != "WAIT":
                found.append(sig)
        except Exception as e:
            state["last_error"] = f"Stock {sym}: {e}"
    # Only notify on new direction per symbol; no repeated message each scan.
    with lock:
        existing = {f'{s["market"]}:{s["symbol"]}:{s["direction"]}' for s in state["signals"]}
        new_sigs = [s for s in found if f'{s["market"]}:{s["symbol"]}:{s["direction"]}' not in existing]
        state["signals"] = (found + state["signals"])[:100]
        state["last_scan"] = datetime.now(timezone.utc).isoformat()
    for s in new_sigs:
        msg = (f"MARKET SIGNAL — {s['market'].upper()}\\n{s['symbol']} | {s['direction']}\\n"
               f"Entry: {s['entry']}\\nSL: {s['stop_loss']}\\nTP: {s['take_profit']}\\n"
               f"RSI: {s['rsi14']} | Target R:R 1:2\\nReasons: {'; '.join(s['reasons'])}\\n"
               "Signal only — verify before making any decision. No order was placed.")
        try: telegram_send(msg)
        except Exception as e: state["last_error"] = f"Telegram notification failed: {e}"
    state["news"] = news_fetch().get("items", [])[:20]
    return {"signals_found": len(found), "new_notifications": len(new_sigs), "news_items": len(state["news"])}


def watcher_loop():
    state["running"] = True
    while not stop_event.is_set():
        try:
            scan_once()
        except Exception as e:
            state["last_error"] = str(e)
        stop_event.wait(SCAN_INTERVAL_SECONDS)
    state["running"] = False


@app.on_event("startup")
def startup():
    if os.getenv("ENABLE_BACKGROUND_SCANNER", "true").lower() == "true":
        t = threading.Thread(target=watcher_loop, daemon=True)
        t.start()


@app.on_event("shutdown")
def shutdown():
    stop_event.set()


class SignalRequest(BaseModel):
    market: Literal["crypto", "stock"] = "crypto"
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    account_balance_pkr: float = Field(default=100000, gt=0)
    risk_pct: float = Field(default=2, gt=0, le=2)
    reward_risk: float = Field(default=2, ge=1, le=5)


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Realtime Market Watcher</title><style>
    body{font-family:system-ui;max-width:850px;margin:20px auto;padding:0 14px;background:#10131b;color:#f4f5f7}
    .card{background:#1b2130;border:1px solid #343c50;border-radius:14px;padding:16px;margin:12px 0}
    button,input,select{font:inherit;padding:11px;border-radius:8px;border:1px solid #566079;width:100%;box-sizing:border-box;margin:5px 0;background:#10131b;color:white}
    button{background:#2e6be6;border:0;font-weight:700}pre{white-space:pre-wrap;overflow-wrap:anywhere}
    </style></head><body><h1>Realtime Market Watcher</h1>
    <p>Crypto + stocks • 1-hour crypto signals • daily stock signals • optional Telegram alerts</p>
    <div class="card"><b>Watcher status</b><pre id="status">Loading…</pre><button onclick="loadStatus()">Refresh status</button><button onclick="scan()">Scan now</button></div>
    <div class="card"><b>Latest signals</b><pre id="signals">Loading…</pre><button onclick="loadSignals()">Refresh signals</button></div>
    <div class="card"><b>Recent headlines</b><pre id="news">News feed optional; configure NEWS_API_KEY.</pre><button onclick="loadNews()">Refresh headlines</button></div>
    <p>Signals are informational, not guaranteed predictions. No trading orders are submitted.</p>
    <script>
    async function loadStatus(){let r=await fetch('/status');document.getElementById('status').textContent=JSON.stringify(await r.json(),null,2)}
    async function loadSignals(){let r=await fetch('/signals');document.getElementById('signals').textContent=JSON.stringify(await r.json(),null,2)}
    async function loadNews(){let r=await fetch('/news');document.getElementById('news').textContent=JSON.stringify(await r.json(),null,2)}
    async function scan(){document.getElementById('status').textContent=JSON.stringify(await (await fetch('/scan-now',{method:'POST'})).json(),null,2);loadSignals()}
    loadStatus();loadSignals();loadNews();setInterval(()=>{loadStatus();loadSignals()},30000);
    </script></body></html>
    """


@app.get("/health")
def health():
    return {"status": "ok", "real_orders_enabled": REAL_ORDERS_ENABLED}


@app.get("/status")
def status():
    return {"app": APP_NAME, "running": state["running"], "last_scan": state["last_scan"],
            "last_error": state["last_error"], "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
            "crypto_watchlist": WATCHLIST, "stock_watchlist": STOCK_WATCHLIST,
            "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
            "news_api_configured": bool(NEWS_API_KEY), "real_orders_enabled": REAL_ORDERS_ENABLED,
            "daily_loss_limit_pct_configured_only": DAILY_LOSS_LIMIT_PCT,
            "note": "This version does not track paper P&L, so daily loss lock is not active yet."}


@app.get("/signals")
def signals():
    return {"count": len(state["signals"]), "signals": state["signals"][:50]}


@app.get("/news")
def news():
    return news_fetch()


@app.post("/scan-now")
def scan_now():
    try:
        return scan_once()
    except Exception as e:
        raise HTTPException(502, str(e))


@app.post("/signal")
def signal(req: SignalRequest):
    try:
        if req.market == "crypto":
            candles = exchange.fetch_ohlcv(req.symbol, timeframe=req.timeframe, limit=150)
            df = pd.DataFrame(candles, columns=["timestamp","open","high","low","close","volume"])
        else:
            hist = yf.Ticker(req.symbol.upper()).history(period="1y", interval="1d", auto_adjust=False)
            if hist.empty: raise HTTPException(404, "No stock data returned. Check ticker symbol.")
            df = hist.rename(columns={"Open":"open","High":"high","Low":"low","Close":"close","Volume":"volume"})
        return technical_signal(df, req.symbol, req.market)
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, str(e))


@app.get("/safety")
def safety():
    return {"mode":"SIGNALS_ONLY", "real_orders_enabled":False, "order_placement_routes":False,
            "max_leverage_cap":MAX_LEVERAGE, "risk_pct_cap":2,
            "daily_loss_lock":"NOT IMPLEMENTED: no paper P&L ledger yet",
            "notification":"Telegram optional; configure bot token and chat ID",
            "political_analysis":"Headlines are collected only; no automated political sentiment score or causal trading claims."}

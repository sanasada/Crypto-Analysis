# Realtime Market Watcher v0.2 — Signal-only

Android-friendly FastAPI starter that periodically checks public OKX crypto candles and daily stock candles, displays technical signals, optionally sends Telegram alerts, and fetches headlines if a NewsAPI key is configured.

## Safety
- Real order placement is hard-disabled; there is no order endpoint.
- No OKX private API keys are needed.
- Signal logic is an illustrative EMA9/EMA21 + RSI14 + ATR strategy, not a validated AI model.
- Crypto scans use 1-hour candles; stocks use daily historical candles.
- News feed is optional and only collects headlines. Political/geopolitical headlines can be false, incomplete or market-irrelevant; verify claims across reliable sources.
- This version does not calculate true PKR-converted contract size, funding, fees, slippage, or liquidation price. The displayed position estimate must not be used as an order quantity.
- Daily loss lock is NOT implemented because a persistent paper-trading ledger does not yet exist. The environment variable is only a planned setting, not an active protection.
- Railway free/sleeping services may pause or restart. Continuous alerts require an always-on deployment and uptime monitoring.

## Deploy from Android
1. Extract ZIP.
2. Create a private GitHub repo and upload all extracted files to its root (not the ZIP itself).
3. Connect repo to Railway and deploy.
4. Open deployment URL, then `/health`, `/status`, `/signals`.
5. The root page has “Scan now” and shows latest signals.

## Telegram notifications (optional)
1. In Telegram, message `@BotFather`, create a bot and copy its token.
2. Start a chat with your new bot and send it `/start`.
3. Get your chat ID using a trusted Telegram bot/getUpdates method; never share it publicly.
4. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in Railway Variables.
5. Redeploy and check `/status` for `telegram_configured: true`.
6. Alerts are sent only for a newly detected symbol/direction in the in-memory signal history; restarts reset that memory.

## News headlines (optional)
1. Obtain a NewsAPI key from the provider.
2. Set `NEWS_API_KEY` in Railway Variables.
3. `/news` displays recent matching headlines and source URLs.
NewsAPI plan availability/latency can vary; do not rely on it as the only source for breaking political news.

## Endpoints
- `GET /health`
- `GET /status`
- `GET /signals`
- `GET /news`
- `GET /safety`
- `POST /scan-now`
- `POST /signal`

## Example signal request
```json
{
  "market": "crypto",
  "symbol": "BTC/USDT",
  "timeframe": "1h",
  "account_balance_pkr": 100000,
  "risk_pct": 2,
  "reward_risk": 2
}
```

## Next milestone before any real money
Add persistent paper ledger, simulated trade tracking, active daily drawdown lock, FX conversion, contract metadata and lot-size rounding, fees/funding/slippage, historical walk-forward backtest, alert deduplication persistence, data freshness checks, tests, and explicit manual approval flow. Keep real orders absent until all this is independently verified.

import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from urllib.parse import urlparse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import yfinance as yf
from apscheduler.schedulers.background import BackgroundScheduler
import asyncio
import requests
from datetime import datetime

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#
# --- Models ---
#

class TrendingTicker(BaseModel):
    symbol:    str
    name:      Optional[str]
    logo_url:  Optional[str]
    price:     Optional[float]

class PriceResponse(BaseModel):
    symbol:        str
    name:          Optional[str]
    price:         float
    day_high:      float
    day_low:       float
    prev_close:    float
    timestamp:     str
    logo_url:      Optional[str]
    currency:      Optional[str]

    market_cap:        Optional[float]
    fifty_two_wk_high: Optional[float]
    fifty_two_wk_low:  Optional[float]
    volume:            Optional[int]
    avg_volume:        Optional[int]
    trailing_pe:       Optional[float]
    forward_pe:        Optional[float]
    eps:               Optional[float]
    dividend_yield:    Optional[float]
    next_earnings:     Optional[str]
    ex_dividend_date:  Optional[str]
    sector:            Optional[str]
    industry:          Optional[str]
    country:           Optional[str]
    website:           Optional[str]

class HistoricalDataPoint(BaseModel):
    date:   str
    open:   float
    high:   float
    low:    float
    close:  float
    volume: int

class MovingAverageResponse(BaseModel):
    symbol:         str
    period:         int
    moving_average: float

class Alert(BaseModel):
    symbol:       str
    condition:    str
    target_price: float
    triggered:    bool = False

class ExchangeConfig(BaseModel):
    region:   str   # e.g. "US" or "IN"
    timezone: str   # IANA tz database name
    open:     str   # "HH:MM"
    close:    str   # "HH:MM"

#
# --- In-memory stores & config ---
#

alerts_db: List[Alert] = []

EXCHANGE_HOURS = {
    "US": ExchangeConfig(
        region="US",
        timezone="America/New_York",
        open="09:30",
        close="16:00",
    ),
    "IN": ExchangeConfig(
        region="IN",
        timezone="Asia/Kolkata",
        open="09:15",
        close="15:30",
    ),
    # add more regions here if needed
}

# Financial Modeling Prep fallback setup
FMP_API_KEY = os.getenv("FMP_API_KEY")
if not FMP_API_KEY:
    raise RuntimeError("Please set FMP_API_KEY in your environment")

# map your region codes to FMP exchange codes
FMP_EXCHANGE = {
    "US": "NASDAQ",
    "IN": "BSE",    # or "NSE"
    "GB": "LSE",
    # add other region→exchange mappings as needed
}

#
# --- Endpoints ---
#

@app.get("/", summary="Root endpoint")
def read_root():
    return {"message": "Welcome to FinSight API 🚀"}

@app.get("/exchange-config", response_model=ExchangeConfig, summary="Get exchange hours & timezone")
def get_exchange_config(region: str = Query("US", min_length=2, max_length=3)):
    cfg = EXCHANGE_HOURS.get(region.upper())
    if not cfg:
        raise HTTPException(404, f"No exchange config for region '{region}'")
    return cfg

@app.get("/price", response_model=PriceResponse, summary="Get current price + metadata")
def get_current_price(symbol: str = Query(..., min_length=1)):
    sym = symbol.strip().upper()
    ticker = yf.Ticker(sym)
    info = ticker.info or {}
    name = info.get("longName") or info.get("shortName")
    logo = info.get("logo_url")
    if not logo:
        site = info.get("website") or ""
        domain = urlparse(site).netloc
        if domain:
            logo = f"https://logo.clearbit.com/{domain}"

    raw_earn = info.get("earningsTimestamp")
    next_earnings = (
        datetime.fromtimestamp(raw_earn).isoformat()
        if isinstance(raw_earn, (int, float))
        else None
    )
    raw_ex = info.get("exDividendDate")
    ex_dividend_date = (
        datetime.fromtimestamp(raw_ex).date().isoformat()
        if isinstance(raw_ex, (int, float))
        else None
    )

    data = ticker.history(period="2d")
    if data.empty or len(data) < 2:
        raise HTTPException(404, "Symbol not found or insufficient data")
    cur, prev = data.tail(2).iloc[-1], data.tail(2).iloc[-2]

    return PriceResponse(
        symbol=sym,
        name=name,
        price=cur["Close"],
        day_high=cur["High"],
        day_low=cur["Low"],
        prev_close=prev["Close"],
        timestamp=str(cur.name),
        logo_url=logo,
        currency=info.get("currency"),
        market_cap=info.get("marketCap"),
        fifty_two_wk_high=info.get("fiftyTwoWeekHigh"),
        fifty_two_wk_low=info.get("fiftyTwoWeekLow"),
        volume=info.get("volume"),
        avg_volume=info.get("averageVolume"),
        trailing_pe=info.get("trailingPE"),
        forward_pe=info.get("forwardPE"),
        eps=info.get("trailingEps"),
        dividend_yield=info.get("dividendYield"),
        next_earnings=next_earnings,
        ex_dividend_date=ex_dividend_date,
        sector=info.get("sector"),
        industry=info.get("industry"),
        country=info.get("country"),
        website=info.get("website"),
    )

@app.get(
    "/trending",
    response_model=List[TrendingTicker],
    summary="Get top trending tickers (region-specific only, FMP fallback)",
)
def get_trending(
    count:  int    = Query(10, ge=1, le=20),
    region: str    = Query("US", min_length=2, max_length=3),
):
    region_code = region.upper()

    def fetch_symbols(url: str, params: dict) -> List[str]:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, params=params, headers=headers, timeout=5)
        r.raise_for_status()
        results = r.json().get("finance", {}).get("result", [])
        if not results or not results[0].get("quotes"):
            raise ValueError("no-quotes")
        return [q["symbol"] for q in results[0]["quotes"]]

    symbols: List[str] = []

    # 1) Only use Yahoo for US region
    if region_code == "US":
        TREND_URL = f"https://query2.finance.yahoo.com/v1/finance/trending/{region_code}"
        SCREENER  = "https://query2.finance.yahoo.com/v1/finance/screener/predefined/saved"

        # Try Trending
        try:
            symbols = fetch_symbols(
                TREND_URL,
                {"formatted":"false","lang":"en-US","region":region_code,"count":count,"corsDomain":"finance.yahoo.com"}
            )
        except Exception:
            # Try Day Gainers
            try:
                symbols = fetch_symbols(
                    SCREENER,
                    {"formatted":"false","lang":"en-US","region":region_code,"count":count,"corsDomain":"finance.yahoo.com","scrIds":"day_gainers"}
                )
            except Exception:
                # Try Most Actives
                try:
                    symbols = fetch_symbols(
                        SCREENER,
                        {"formatted":"false","lang":"en-US","region":region_code,"count":count,"corsDomain":"finance.yahoo.com","scrIds":"most_actives"}
                    )
                except Exception:
                    symbols = []

    # 2) Fallback to FMP for any region without symbols
    if not symbols:
        exch = FMP_EXCHANGE.get(region_code, "NASDAQ")
        fmp_url    = "https://financialmodelingprep.com/api/v3/stock-screener"
        fmp_params = {
            "exchange": exch,
            "limit":    count,
            "apikey":   FMP_API_KEY,
            "sort":     "changesPercentage",
            "order":    "desc",
        }
        resp = requests.get(fmp_url, params=fmp_params, timeout=5)
        if not resp.ok:
            raise HTTPException(502, f"FMP fallback failed ({resp.status_code})")
        data = resp.json()
        symbols = [item["symbol"] for item in data]

    if not symbols:
        raise HTTPException(502, f"No trending/gainers for region '{region_code}'")

    # 3) Populate details via get_current_price()
    out: List[TrendingTicker] = []
    for sym in symbols:
        try:
            pr = get_current_price(symbol=sym)
            out.append(TrendingTicker(
                symbol=   pr.symbol,
                name=     pr.name,
                logo_url= pr.logo_url,
                price=    pr.price,
            ))
        except HTTPException:
            out.append(TrendingTicker(symbol=sym, name=None, logo_url=None, price=None))

    return out

@app.get("/history", response_model=List[HistoricalDataPoint])
def get_historical_data(
    symbol: str,
    range: str = Query("1mo", description="Data range, e.g. 1d, 5d, 1mo, 1y")
):
    ticker = yf.Ticker(symbol)
    data = ticker.history(period=range)
    if data.empty:
        raise HTTPException(status_code=404, detail="Symbol not found or no historical data")
    return [
        HistoricalDataPoint(
            date=str(idx.date()),
            open=row["Open"],
            high=row["High"],
            low=row["Low"],
            close=row["Close"],
            volume=row["Volume"]
        )
        for idx, row in data.iterrows()
    ]

@app.get("/moving-average", response_model=MovingAverageResponse, summary="Get moving average")
def get_moving_average(symbol: str, period: int = 50):
    ticker = yf.Ticker(symbol)
    data = ticker.history(period=f"{period + 10}d")
    if data.empty:
        raise HTTPException(status_code=404, detail="Symbol not found")
    ma = data["Close"].tail(period).mean()
    return MovingAverageResponse(
        symbol=symbol.upper(),
        period=period,
        moving_average=round(ma, 2)
    )

@app.post("/alerts", summary="Create a price alert")
def create_alert(alert: Alert):
    alert.symbol = alert.symbol.upper()
    alerts_db.append(alert)
    return {"message": "Alert created successfully", "alert": alert}

@app.get("/alerts", summary="List all alerts")
def list_alerts():
    return alerts_db

@app.websocket("/ws/price/{symbol}")
async def websocket_price(websocket: WebSocket, symbol: str):
    await websocket.accept()
    ticker = yf.Ticker(symbol)
    try:
        while True:
            data = ticker.history(period="1d").tail(1).iloc[-1]
            payload = {
                "price":     data["Close"],
                "high":      data["High"],
                "low":       data["Low"],
                "timestamp": str(data.name),
            }
            await websocket.send_json(payload)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass

def check_alerts():
    print("[AlertChecker] Checking alerts...")
    for alert in alerts_db:
        if alert.triggered:
            continue

        ticker = yf.Ticker(alert.symbol)
        data = ticker.history(period="1d")
        if data.empty:
            continue

        last_close = data["Close"].iloc[-1]
        triggered = (
            (alert.condition == "above" and last_close > alert.target_price) or
            (alert.condition == "below" and last_close < alert.target_price)
        )

        if triggered:
            alert.triggered = True
            msg = (
                f"{alert.symbol} is now "
                f"{'above' if alert.condition=='above' else 'below'} "
                f"${alert.target_price:.2f} (current ${last_close:.2f})"
            )
            print(f"✅ ALERT: {msg}")

            try:
                requests.post(
                    "http://localhost:3000/api/push/send",
                    json={"title": f"{alert.symbol} Alert 🚨", "body": msg},
                    timeout=5
                )
            except Exception as e:
                print("❌ Push notification failed:", e)

scheduler = BackgroundScheduler()
scheduler.add_job(check_alerts, "interval", seconds=30, id="alert_checker")
scheduler.start()

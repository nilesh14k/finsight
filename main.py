from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import yfinance as yf
from apscheduler.schedulers.background import BackgroundScheduler
import asyncio
import requests

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class PriceResponse(BaseModel):
    symbol: str
    price: float
    day_high: float
    day_low: float
    prev_close: float
    timestamp: str

class HistoricalDataPoint(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int

class MovingAverageResponse(BaseModel):
    symbol: str
    period: int
    moving_average: float

class Alert(BaseModel):
    symbol: str
    condition: str
    target_price: float
    triggered: bool = False

alerts_db: List[Alert] = []

@app.get("/", summary="Root endpoint")
def read_root():
    return {"message": "Welcome to FinSight API 🚀"}

@app.get("/price", response_model=PriceResponse)
def get_current_price(
    symbol: str = Query(..., min_length=1, description="Ticker symbol, e.g. AAPL")
):
    symbol = symbol.strip().upper()
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="2d")
    except ValueError:
        raise HTTPException(400, "Invalid symbol")
    if data.empty or len(data) < 2:
        raise HTTPException(404, "Symbol not found or insufficient data")
    last_two = data.tail(2)
    current, prev = last_two.iloc[-1], last_two.iloc[-2]
    return PriceResponse(
        symbol=symbol,
        price=current["Close"],
        day_high=current["High"],
        day_low=current["Low"],
        prev_close=prev["Close"],
        timestamp=str(current.name),
    )

@app.get("/history", response_model=List[HistoricalDataPoint], summary="Get historical price data")
def get_historical_data(symbol: str, range: str = Query("1mo", description="Data range, e.g. 1d, 5d, 1mo, 1y")):
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
        ) for idx, row in data.iterrows()
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
                "price": data["Close"],
                "high": data["High"],
                "low": data["Low"],
                "timestamp": str(data.name)
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
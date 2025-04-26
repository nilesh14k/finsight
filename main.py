# main.py
from fastapi import FastAPI, Query
from pydantic import BaseModel
import yfinance as yf
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware

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

@app.get("/")
def read_root():
    return {"message": "Welcome to FinSight API 🚀"}

@app.get("/price", response_model=PriceResponse)
def get_current_price(symbol: str):
    ticker = yf.Ticker(symbol)
    data = ticker.history(period="1d")
    if data.empty:
        return {"error": "Symbol not found"}
    last_quote = data.iloc[-1]
    return PriceResponse(
        symbol=symbol,
        price=last_quote["Close"],
        day_high=last_quote["High"],
        day_low=last_quote["Low"],
        prev_close=last_quote["Open"],
        timestamp=str(data.index[-1])
    )

class HistoricalDataPoint(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int

@app.get("/history", response_model=List[HistoricalDataPoint])
def get_historical_data(symbol: str, range: str = Query("1mo")):
    ticker = yf.Ticker(symbol)
    data = ticker.history(period=range)
    return [
        HistoricalDataPoint(
            date=str(index.date()),
            open=row["Open"],
            high=row["High"],
            low=row["Low"],
            close=row["Close"],
            volume=row["Volume"]
        ) for index, row in data.iterrows()
    ]

class MovingAverageResponse(BaseModel):
    symbol: str
    period: int
    moving_average: float

@app.get("/moving-average", response_model=MovingAverageResponse)
def get_moving_average(symbol: str, period: int = 50):
    ticker = yf.Ticker(symbol)
    data = ticker.history(period=f"{period + 10}d")
    ma = data["Close"].tail(period).mean()
    return MovingAverageResponse(
        symbol=symbol,
        period=period,
        moving_average=round(ma, 2)
    )

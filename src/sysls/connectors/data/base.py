from abc import ABC, abstractmethod
from typing import List
from datetime import datetime
from dataclasses import dataclass


@dataclass
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass
class Quote:
    symbol: str
    timestamp: datetime
    bidPrice: float
    bidSize: float
    askPrice: float
    askSize: float

@dataclass
class Trade:
    symbol: str
    timestamp: datetime
    price: float
    size: float

@dataclass
class GreekSnapshot:
    symbol: str
    strike: float
    timestamp: datetime
    price: float
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float

@dataclass
class OptionChain:
    expiry: datetime
    underlyingSymbol: str
    GreekSnapshot: GreekSnapshot


class DataConnector(ABC):
    """
    Generic interface to fetch data from a data connection.
    """
    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote:
        """Get quotes for a symbol."""
        pass

    @abstractmethod
    async def get_option_chain(self, symbol: str) -> OptionChain:
        """Get option chain for an underlying."""
        pass


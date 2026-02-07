from typing import List
from datetime import datetime
from tastytrade import Session, DXLinkStreamer
from tastytrade.instruments import get_option_chain
from tastytrade.dxfeed import Quote
from ..data.base import DataConnector, Quote, Bar
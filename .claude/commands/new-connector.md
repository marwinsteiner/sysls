Implement a new data connector for $ARGUMENTS.

Follow these steps:
1. Read the DataConnector ABC in src/sysls/data/connector.py.
2. Read an existing connector (e.g. polygon.py) for reference patterns.
3. Create the new connector file in src/sysls/data/.
4. Implement all abstract methods: connect(), subscribe(), get_historical_bars(), get_historical_trades(), stream().
5. Ensure all output data is normalized through normalize.py into the standard MarketDataEvent schema.
6. Handle authentication, rate limiting, pagination, and reconnection.
7. Support both streaming (live) and historical (backtest) modes.
8. Write tests in tests/data/ including mock responses for the external API.
9. Add configuration schema to core/config.py if new settings are needed.

Key requirements:
- All emitted events must be normalized MarketDataEvents.
- Historical data must be compatible with ArcticDB storage via store.py.
- Streaming mode must emit events to the event bus.
- Include proper error handling and structured logging via structlog.

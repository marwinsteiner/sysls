Implement a new venue adapter for $ARGUMENTS.

Follow these steps:
1. Read the VenueAdapter ABC in src/sysls/execution/venues/base.py to understand the interface contract.
2. Read an existing adapter (e.g. ccxt_venue.py) for reference on patterns and conventions.
3. Create the new adapter file in src/sysls/execution/venues/.
4. Implement all abstract methods: connect(), disconnect(), submit_order(), cancel_order(), get_positions(), get_balances().
5. Handle authentication, rate limiting, and reconnection.
6. Use the event bus to emit OrderAccepted, OrderRejected, FillEvent, ConnectionEvent.
7. Write comprehensive tests in tests/execution/venues/.
8. Add the venue to the registry in src/sysls/execution/venues/__init__.py.

Remember:
- Venue adapters must be thin — no business logic, just API translation.
- Use Decimal for all prices and quantities.
- All I/O must be async.
- Wrap venue-specific exceptions in VenueError.
- Include paper trading support via the standard paper trading infrastructure.

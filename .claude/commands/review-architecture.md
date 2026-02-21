Review the current codebase for architecture consistency and adherence to CLAUDE.md conventions.

Check the following:
1. **Layer separation**: No imports crossing layer boundaries (e.g. execution must not import from data or vice versa). All inter-layer communication must go through the event bus.
2. **Event contracts**: All events in core/events.py are immutable. No event mutation anywhere in the codebase.
3. **Type safety**: All public functions have complete type annotations. No `Any` types except where genuinely unavoidable.
4. **Async discipline**: All I/O is async. No blocking calls on the event loop. CPU-heavy work offloaded to thread/process pools.
5. **Financial precision**: Prices and quantities in execution path use Decimal, not float.
6. **No global state**: No module-level mutable variables. All state is in classes passed via dependency injection.
7. **Testing**: Each module has corresponding tests. Async tests use pytest-asyncio.
8. **Logging**: All logging uses structlog. No print() statements.
9. **Error handling**: Custom exceptions inherit from SyslsError. No bare Exception catches in business logic.
10. **Docstrings**: All public classes and functions have Google-style docstrings.

Report findings as a prioritized list of issues with file paths and line numbers.

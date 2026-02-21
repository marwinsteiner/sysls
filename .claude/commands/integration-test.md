Write integration tests for the interaction between $ARGUMENTS.

Integration tests in sysls verify that components work together through the event bus.

Pattern:
1. Set up an Engine instance with the relevant components wired together.
2. Use a test Clock in simulated mode so tests are deterministic.
3. Publish input events and assert on output events captured by a test subscriber.
4. Verify state changes in the OMS, risk engine, or data store as appropriate.

Example skeleton:
```python
import pytest
from sysls.core.bus import EventBus
from sysls.core.clock import Clock, ClockMode

@pytest.fixture
async def bus():
    b = EventBus()
    await b.start()
    yield b
    await b.stop()

@pytest.fixture
def clock():
    return Clock(mode=ClockMode.SIMULATED)

@pytest.mark.asyncio
async def test_data_to_strategy_to_execution(bus, clock):
    # Wire up components
    # Publish a MarketDataEvent
    # Assert a SignalEvent was emitted
    # Assert an OrderSubmitted was emitted
    # Assert risk checks passed
    pass
```

Key principles:
- Integration tests go in tests/integration/.
- Use real event bus, mock only external APIs (venue REST/WS, data provider APIs).
- Tests must be deterministic — use simulated clock, fixed random seeds.
- Test both happy paths and failure modes (venue disconnect, risk breach, etc.).

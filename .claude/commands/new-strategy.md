Create a new example strategy for $ARGUMENTS.

Follow these steps:
1. Read the Strategy ABC in src/sysls/strategy/base.py.
2. Review existing examples in examples/ for patterns and conventions.
3. Create the strategy file in examples/.
4. Implement the required methods:
   - on_start(): initialization, subscribe to instruments
   - on_market_data(event): process incoming data, update internal state
   - generate_signals(): emit SignalEvents based on current state
   - construct_portfolio(signals): convert signals to target portfolio weights
   - on_fill(event): handle order fills, update tracking state
   - on_stop(): cleanup
5. Define the strategy's universe (instruments, venues, data sources).
6. Document the strategy's thesis, parameters, and expected behavior.
7. Include a backtest configuration that demonstrates the strategy works.

Remember:
- Strategies must be clock-agnostic (work in live and backtest without modification).
- Use the event bus — don't call venue adapters directly.
- Respect the risk framework — emit signals, let portfolio construction and risk handle sizing.
- Use Decimal for price/quantity calculations.
- Add a docstring explaining the strategy's economic intuition.

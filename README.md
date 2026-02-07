# sysls

`sysls` is a systematic multi-asset long-short strategy framework designed to help individuals build systematic
long/short portfolios across different asset classes. It provides a clean separation between:

- data connectors (market data access, option chains, Greeks, index universes)
- risk connectors (paper trading or connection to brokerages/exchanges)
- strategy definitions (user-implemented strategies)

The first supported broker/data source is tastytrade via the unofficial Python SDK, with a focus on equities, listed
options, futures, and futures options. Users extend a base `Strategy` class, plug in a data connector and an optional
risk/execution connector, and implement their own signal-generation logic (for example, use ATM call vs. put IV skew as
a long/short signal for an equity universe such as the S&P100.)

Configuration and secrets are handled via Dynaconf.

## Design Overview

At a high level, the framework is built arouind three core abstractions:

1. Data Connector
    - Fetches prices
    - Provides generic methods for strategies
2. Risk Connector
    - Places and manages orders, exposes positions
    - Can connect to brokerages or a local paper-trading engine
        - `PaperRiskConnector`
        - `TastytradeRiskConnector`
3. Strategy
    - Base class that users extend.
    - Requires a `DataConnector`, may receive a `RiskConnector` (default is paper)
    - Operates on a universe of symbols (e.g. `sp100`, `sp500`, `ndx`, `rut2000`) loaded via `yfinance`
    - Users implement `generate_signals()` to return a list of orders (long/short), given the current data.

Example use case:

- Reconstruct the at-the-money implied volatility skew for each stock in the `sp100` (OEX).
- If the ATM call IV exceeds the ATM put IV, long the stock
- If the ATM put IV exceeds the ATM call IV, short the stock
- Send these equity orders through the configured risk connector (paper or live).

## Getting Started

1. Install dependencies: using your preferred Python environment manager (I recommend Astral's `uv`) install
   dependencies listed in `pyproject.toml`
2. Configure settings: `dynaconf init -f json` at the project root. Fill in the relevant credentials in the
   `.secrets.json` file.
   [sample of the file goes here]
3. Write or use a strategy
    - Extend the base `Strategy` class in `sysls.strategy.base`.
    - Use the generic `DataConnector` methods to fetch prices, chains, or Greeks.
    - Return a list of `Order` objects from `generate_signals`.
      Example strategy implemented in the examples directory.
4. Run a strategy
    - Wire up a simple runner that:
        - Instantiates your chosen `DataConnector` and `RiskConnector`
        - Creates your `Strategy` subclass with a universe (e.g. `sp100`)
        - Calls `strategy.rebalance()`
    - Start in paper mode, then switch the risk connector when satisfied.

## Contributing

Contributions are encouraged. Building and maintaining `sysls` is a big undertaking. Examples of useful contributions
include:à

- New data connectors (e.g. IBKR, other brokers, data vendors, crypto exchanges for example through `ccxt`)
- New risk/execution connectors
- Example long-short strategies(equities, options, futures, multi-asset)
- Universe builders for additional indices or custom universes
- Backtesting and analytics tooling
- Tests, documentation, and CI improvements.
  Please open an issue to discuss major changes before submitting a PR.

## Roadmap

This is an evolving project. A rough roadmap:

0. Implement all the base classes (in progress)
1. tastytrade integration (in progress)
    - Implement `TastytradeDataConnector` (spot, option chains, Greeks, candles)
    - Implement `TastytradeRiskConnector` (order placement, basic risk/position views)
    - Provide a few example strats
2. Additional brokers and data sources
    - Implement IBKR connectors (Use `ibasync`!!)
    - Add hooks for other retail-accessible brokers and possibly purely data-oriented connectors, e.g. DataBento or
      Polygon
    - Standardize connector interfaces so strategies remain broker-agnostic.
3. Vectorized backtesting layer
    - Introduce a backtesting engine that:
        - Uses the same strategy and connector abstractions
        - Can run historical simulations in a vectorized fashion
    - Support
        - Multiple universes, factor-style long-short portfolios
        - Realistic transaction cost/slippage modelling
        - Analytics on top (performance reports and diagnostics)
4. Infra and ergonomics
    - CLI for running strategies, scheduling, and switching environments
    - Documentation and cookbook examples for common strategy types

If you are interested in contributing to any part of the roadmap, feel free to open an issue and outline what you'd like
to work on.
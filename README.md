We're building an ETF-like platform for prediction markets that lets users invest in diversified portfolios based on real-world trends and financial theses. The platform aggregates markets across exchanges like Polymarket and Kalshi, using Al to identify relevant contracts and a quantitative optimization engine to construct portfolios based on risk, correlation, and liquidity. A real-time news analysis system continuously classifies headlines, estimates their impact on event probabilities, and dynamically rebalances portfolios to capitalize on potential market mispricings. Under the hood, we combine probabilistic modeling, cross-market arbitrage detection, and automated trading to turn fragmented prediction markets into intelligent, actively managed financial instruments.

## News-driven ETF rebalancing

The [integration guide](INTEGRATION.md) covers the bundled Ridge model and
LangChain news agent, cached evidence, ETF-only allocation policy, and durable
exchange reconciliation. Install the combined Python dependencies from this
repository root with `python -m pip install -r requirements-news.txt`. External
execution stays disabled until explicitly configured; the feature includes an
offline paper/mocked validation path.

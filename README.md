We're building an ETF-like platform for prediction markets that lets users invest in diversified portfolios based on real-world trends and financial theses. The platform aggregates markets across exchanges like Polymarket and Kalshi, using Al to identify relevant contracts and a quantitative optimization engine to construct portfolios based on risk, correlation, and liquidity. A real-time news analysis system continuously classifies headlines, estimates their impact on event probabilities, and dynamically rebalances portfolios to capitalize on potential market mispricings. Under the hood, we combine probabilistic modeling, cross-market arbitrage detection, and automated trading to turn fragmented prediction markets into intelligent, actively managed financial instruments.

## News-driven ETF rebalancing

The [integration guide](INTEGRATION.md) covers the bundled Ridge model and
LangChain news agent, cached evidence, ETF-only allocation policy, and durable
exchange reconciliation. Install the combined Python dependencies from this
repository root with `python -m pip install -r requirements-news.txt`. External
execution stays disabled until explicitly configured; the feature includes an
offline paper/mocked validation path.

## Run locally

You need Python 3.12+ and Node 20+. Run the backend and the website in **two separate terminals**, both starting from the repository root.

### 1. Backend (API)

```bash
python3 -m venv .venv                                  # first time only
.venv/bin/python -m pip install -r requirements-news.txt   # first time only
.venv/bin/python scripts/run_api.py
```

The API runs at http://127.0.0.1:8000 (endpoint docs at `/docs`). On first start it syncs Kalshi markets in the background, which takes about a minute.

Building baskets and news monitoring need a Meta Model API key. Put it in a `.env` file at the repository root:

```
META=LLM_your_key_here
```

Without a key, the site still runs, but thesis search returns an error.

### 2. Website

```bash
cd web
npm install        # first time only
npm run dev
```

Open http://localhost:3000. Use `localhost`, not the network address: the API only accepts requests from `http://localhost:3000` by default.

To stop either one, press `Ctrl + C` in its terminal.

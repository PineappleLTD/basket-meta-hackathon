# optimizer
given the contracts the LLM picked, decide what share of the budget goes into each.

## input 
| Column      | Meaning                                              |
| ----------- | ---------------------------------------------------- |
| `market_id` | contract ID                                          |
| `price`     | price of the chosen side (Yes or No), 0–1            |
| `edge`      | estimated mispricing, `q - p` (from the ridge).      |
| `relevance` | fit to the thesis, 0–1 (from the LLM)                |
| `volume`    | daily volume, USD                                    |


**inputs to add**: 

*Time to resolution:* Right now an 8-point edge counts the same whether the market settles next week or next year. Dividing edge by days to expiry favors contracts that pay off sooner, so money isn’t locked up for months for the same profit.

*Bid-ask spread:* Buying costs more than the listed price because you pay the spread, and that eats into the edge. Subtracting it from expected return means contracts with wide spreads need a bigger edge to earn weight.


**initial results (on mock data)**:


**Headline arrives:** impact model says `us_ai_bill` is underpriced by 8 points.

| Contract      | Before | After  | Change  |
| ------------- | ------ | ------ | ------- |
| `chip_export` | 22.14% | 22.14% | 0       |
| `eu_ai_act`   | 21.96% | 21.96% | 0       |
| `us_ai_bill`  | 19.62% | 25.00% | **+5.38** |
| `openai_fine` | 9.01%  | 9.01%  | 0       |
| `eu_ai_delay` | 8.04%  | 8.04%  | 0       |

## Workflow

**1. Thesis → picks.** The user types a thesis. The Muse API (LLM) picks contracts from the
markets the backend has synced from Kalshi (Polymarket planned), chooses Yes or No for each,
and scores each pick's relevance to the thesis.

**2. Edge (ridge regression).** A ridge model, trained on past headlines and the price moves
that followed, estimates how mispriced each pick is (`edge`). With no recent news, edge is 0.

**3. Optimizer.** Takes each pick's relevance (LLM), edge (ridge), and market data (price,
volume, bid-ask spread, time to resolution, price history) and returns what percentage of the
budget goes into each pick.

**4. Continuity (background, on a schedule).** Pull new headlines from a news API → tag them
with a cheap classifier or LLM → ridge model updates `edge` → rerun the optimizer with the
current weights. It only changes the basket when the expected gain beats the trading cost.
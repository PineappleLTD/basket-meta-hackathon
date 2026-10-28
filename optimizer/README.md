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


**initial results (on mock data) **:
Headline arrives: impact model says us_ai_bill is underpriced by 8 points
  market_id  weight_before  weight_after  change
chip_export         0.2214        0.2214  0.0000
  eu_ai_act         0.2196        0.2196  0.0000
 us_ai_bill         0.1962        0.2500  0.0538
openai_fine         0.0901        0.0901  0.0000
eu_ai_delay         0.0804        0.0804  0.0000
# Reference

Read the section you need. Do not paste this file into the user-facing answer.

## Statement mapping

Map names before calculating. Same label is not the same concept across GAAP.

| Concept | US GAAP tendency | Chinese GAAP / A-share |
|---|---|---|
| Revenue | Revenue, net sales | 营业收入 |
| Gross profit | Revenue − cost of revenue | 营业收入 − 营业成本. Do not subtract 税金及附加 into gross profit unless the user defines it that way |
| Operating profit | Operating income | 营业利润 includes 其他收益, investment income, impairments. It is not EBIT |
| EBIT (constructed) | Operating income ± adjustments | 利润总额 + 利息费用 − 利息收入, or 营业利润 stripped of non-operating items. State which bridge you used |
| Net income to parent | Net income attributable to parent | 归属于母公司股东的净利润 |
| CFO | Net cash from operating activities | 经营活动产生的现金流量净额 |
| Capex | Purchases of PPE (use cash flow, not the accrual delta alone) | 购建固定资产、无形资产和其他长期资产支付的现金 |
| Cash | Cash and equivalents | 货币资金. Separate restricted cash when disclosed |
| Debt | Short-term borrowings + current portion + long-term debt + leases if the capital structure includes them | 短期借款 + 一年内到期的非流动负债 + 长期借款 + 应付债券 + lease liabilities if in scope |

Identity to check: 资产 = 负债 + 所有者权益, within rounding. Break it before interpreting ratios.

## Ratios

Denominators use averages (begin + end) / 2 when both points exist. State when you used an ending balance.

| Ratio | Formula | Watch |
|---|---|---|
| Gross margin | (Revenue − COGS) / Revenue | COGS definition |
| Operating margin | Operating income / Revenue | One-offs inside operating income |
| Net margin | Net income / Revenue | Parent vs consolidated |
| ROE | Net income / Average equity | Negative equity makes it meaningless |
| ROA | Net income / Average assets | Financial firms: do not compare to industrials |
| ROIC | NOPAT / Invested capital | Define invested capital in the docstring |
| Current | Current assets / Current liabilities | Inventory quality |
| Quick | (Cash + marketable securities + receivables) / Current liabilities | Do not include inventory |
| Interest coverage | EBIT / Interest expense | Interest income is not a reduction of the denominator unless you switched to net interest |
| Net debt / EBITDA | (Debt − cash) / EBITDA | Net cash: report the level, not a fake negative leverage story |
| DSO | Average receivables / Revenue × days | Use credit sales if disclosed |
| DIO | Average inventory / COGS × days | Not revenue |
| FCF | CFO − capex | State if you used maintenance capex only |
| FCFF | EBIT × (1 − tax) + D&A − capex − ΔNWC | Tax is marginal cash tax when book tax is distorted |

DuPont: ROE = (Net income / Revenue) × (Revenue / Average assets) × (Average assets / Average equity).

NOPAT = EBIT × (1 − tax). Do not start NOPAT from 净利润 unless you reverse interest and non-operating items and show the bridge.

## Valuation

FCFF discount rate is WACC. FCFE discount rate is cost of equity. Do not mix.

```
WACC = ke × E / (D + E) + kd × (1 − tax) × D / (D + E)
ke   = rf + β × ERP          # CAPM unless the user specified another model
V0   = Σ FCFF_t / (1 + WACC)^t + VT / (1 + WACC)^N
Gordon terminal: VT = FCFF_{N+1} / (WACC − g), with g < WACC and g near long-run nominal growth
```

- Market D and E for WACC weights, not book, unless the user is doing an APV or a private-company case with a stated target structure.
- Mid-year convention only if you use it consistently in the explicit period and the terminal value.
- Exit multiple: the multiple must be consistent with the cash flow (EV/EBITDA with FCFF, P/E with FCFE). Cross-check implied g against the Gordon g.
- Always show a two-way table on WACC and g (or exit multiple). The point estimate is not the result.
- Per-share value uses diluted shares and subtracts (or adds) the non-operating bridge you claimed: net debt, minorities, associates, excess cash. List the bridge.

Relative value:

- Numerator and denominator from the same period (LTM or forward, not mixed).
- Exclude or footnote outliers instead of silently dropping them. Median is the default aggregator.
- State currency and share-count basis.

## Forecasting

- Forecast drivers (volume, price, margin, days, capex/revenue), then let the statements fall out.
- Tie the three statements: net income → retained earnings, cash flow → cash, balance sheet balances.
- Circularity (interest on average debt): iterate to convergence or use beginning balances and say so.
- History shorter than 3 years: widen the sensitivity, do not invent a normalized margin without labeling it.

## Risk and returns

- Simple return: `P_t / P_{t-1} − 1`. Log return: `log(P_t / P_{t-1})`. Do not mix them in one compounded wealth path.
- Annualize volatility with `std × sqrt(periods)` only for roughly independent periods. State the periods per year.
- Max drawdown is on the wealth index, not on the return series.
- No optimizer until constraints, benchmark, and rebalance rule are written down.

## Sanity checks

Fail the run, do not narrate around it, when:

- Assets ≠ liabilities + equity beyond rounding
- Margins fall outside [−1, 1] unless the business actually did (then footnote)
- WACC ≤ g, or g > long-run nominal GDP + inflation by a wide margin without a reason
- Shares, prices, or FX are zero or negative
- A "growth rate" was computed on a sign change (loss to profit). Report levels instead
- Currency units changed mid-model (元 vs 万元) without an explicit scale factor

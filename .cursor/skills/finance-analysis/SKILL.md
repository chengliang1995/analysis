---
name: finance-analysis
description: >-
  Performs financial statement analysis, valuation, forecasting, and risk work,
  and implements the calculations as reproducible code. Use when the user asks
  for 金融分析, 财报分析, 财务比率, 估值, DCF, 可比公司, 盈利预测, 现金流,
  投资组合, 风险, 量化, or to build financial models, analysis scripts,
  notebooks, or dashboards.
---

# Finance Analysis

Financial conclusions come from code the agent can re-run, not from memorized numbers. Match the user's language. Default stack is Python 3 with pandas and numpy.

## Non-negotiables

- Do not invent prices, filings, ratios, or model outputs. Missing data stays missing; label every assumption.
- Every figure in the write-up must trace to a source or a function output. If code and prose disagree, fix the prose.
- Functions take decimals (`0.08`), not percents (`8`). Format percents only when presenting.
- Do not `fillna(0)` on prices, shares, or rates. Zero is allowed only when zero is the economic meaning.
- Keep full precision in calculations. Round only in the presentation layer and state the rule.
- This is analysis, not personalized investment advice. State that when the conclusion is a buy, sell, or allocation.

## Workflow

1. Write the decision the analysis supports in one sentence. If the user did not specify, state the assumed question and continue.
2. Inventory inputs: entity, period, currency, fiscal year-end, frequency, and what is missing.
3. Pick a method from the table below. Read [reference.md](reference.md) before coding valuation, ratios, or statement mapping.
4. Implement calculations as pure functions, then run them. Do not narrate results before the run.
5. Sanity-check: accounting identity, units (元 / 万元 / 亿元), sign of cash flows, growth vs level, and order of magnitude.
6. Deliver with the template below.

```
Progress:
- [ ] Decision question stated
- [ ] Inputs and gaps listed
- [ ] Method chosen
- [ ] Calculations implemented and run
- [ ] Sanity checks passed
- [ ] Write-up matches code output
```

## Method choice

| Question | Method |
|---|---|
| Is the business healthy? | Statement analysis + ratios |
| What is it worth on fundamentals? | FCFF or FCFE DCF, plus sensitivity on WACC and g |
| Is it cheap vs peers? | Multiples; peers must match business, size, and accounting |
| What happens next year? | Driver-based forecast; percent-of-sales only if drivers are unavailable |
| How risky is the book? | Returns, volatility, drawdown, exposure; constraints before any optimizer |

Use one primary method. A second method is a cross-check, not a second story.

## Code

Layout:

- `formulas.py` — pure functions. Docstring states the formula, units, and sign convention.
- `io_*.py` — loading and column mapping only.
- `run_*.py` or a notebook — parameters, call, export.

Rules:

- No hardcoded tickers, prices, or tax rates inside library functions.
- Public functions have type hints.
- Each non-trivial formula gets one closed-form check (Gordon growth, DuPont identity, or a hand-computed ratio). See [examples.md](examples.md).
- Fiscal year-end is a parameter. Do not assume 31 Dec.
- Money math that must match to the cent uses `Decimal`; analytics on returns may use float64.
- For Chinese GAAP / A-share filings, map line items explicitly. Do not treat 营业利润 as EBIT.

Excel is an export, not the calculation engine, unless the user asked for a workbook model.

## Output template

```markdown
# [Entity] — [question]

**结论：** one sentence a decision-maker can use.
**不是投资建议。** 关键假设：[list]. 数据截止：[date].

## 数据
- 来源、币种、单位、财年截止日
- 缺失项及处理方式

## 结果
| 指标 | 值 | 口径 |
|---|---|---|
| ... | ... | formula or line item |

## 驱动与敏感度
What moves the answer, with the range you actually computed.

## 局限
Accounting mismatches, one-offs, thin history, peer gaps.
```

Lead with the conclusion. Put formula dumps in code, not in the summary.

## Additional resources

- Formulas, statement mapping, and sanity checks: [reference.md](reference.md)
- Function and check patterns: [examples.md](examples.md)

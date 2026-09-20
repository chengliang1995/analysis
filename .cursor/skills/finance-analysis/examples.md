# Examples

## Closed-form checks

Gordon growth. Value is next cash flow over the spread, not the trailing cash flow.

```python
def gordon_value(cash_flow_next: float, discount_rate: float, growth: float) -> float:
    """V0 = CF1 / (r - g). Rates are decimals. Requires r > g."""
    if discount_rate <= growth:
        raise ValueError("discount_rate must exceed growth")
    return cash_flow_next / (discount_rate - growth)


def test_gordon_value() -> None:
    # CF1 = 100, r = 10%, g = 5% → 2000
    assert gordon_value(100.0, 0.10, 0.05) == 2000.0
```

DuPont identity. Build ROE from the three components and match the direct ratio.

```python
def roe_dupont(net_margin: float, asset_turnover: float, equity_multiplier: float) -> float:
    return net_margin * asset_turnover * equity_multiplier


def test_dupont() -> None:
    # 8% margin, 1.25x turnover, 2.0x leverage → 20%
    assert abs(roe_dupont(0.08, 1.25, 2.0) - 0.20) < 1e-12
```

FCFF sign. A working-capital increase is a use of cash.

```python
def fcff(ebit: float, tax_rate: float, da: float, capex: float, delta_nwc: float) -> float:
    """capex and delta_nwc are cash outflows when positive."""
    return ebit * (1.0 - tax_rate) + da - capex - delta_nwc


def test_fcff_nwc_increase_reduces_fcff() -> None:
    base = fcff(100.0, 0.25, 10.0, 20.0, 0.0)
    with_nwc = fcff(100.0, 0.25, 10.0, 20.0, 5.0)
    assert with_nwc == base - 5.0
```

## Column mapping, not silent renames

```python
A_SHARE_MAP = {
    "revenue": "营业收入",
    "cogs": "营业成本",
    "operating_profit": "营业利润",  # not EBIT
    "net_income_parent": "归属于母公司股东的净利润",
    "cfo": "经营活动产生的现金流量净额",
    "capex": "购建固定资产、无形资产和其他长期资产支付的现金",
}


def require_columns(frame, mapping: dict[str, str]):
    missing = [src for src in mapping.values() if src not in frame.columns]
    if missing:
        raise KeyError(f"missing columns: {missing}")
```

## What good output looks like

```markdown
# 示例公司 — 主业是否在变好

**结论：** 2022–2024 毛利率从 28% 升到 34%，但 ROIC 仍约 7%，低于这里用的 9% WACC，主业还没有赚回资本成本。
**不是投资建议。** 关键假设：税率 25%，投入资本不含超额现金。数据截止：2024 年报。

## 数据
- 来源：合并报表，人民币万元，财年截止 12-31
- 缺失：未单列利息费用，EBIT 用「利润总额 + 财务费用」近似，已在口径列注明

## 结果
| 指标 | 2024 | 口径 |
|---|---|---|
| 毛利率 | 34% | (营业收入 − 营业成本) / 营业收入 |
| ROIC | 7.1% | NOPAT / 平均投入资本 |

## 驱动与敏感度
毛利率每降 1 个百分点，ROIC 约降 0.4 个百分点（模型内重算，不是外推）。

## 局限
营业利润含政府补助，未当作主业利润。
```

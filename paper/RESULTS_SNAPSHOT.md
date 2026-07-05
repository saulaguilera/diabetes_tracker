# Results snapshot — 2026-07-05

Numbers as of today, computed from resolved `PredictionAudit` rows (DB export
`diabetes_20260705_1346`). Regenerate with `python3 paper/baselines.py 60` and
the ad-hoc scripts referenced in each section.

## Live track record (per-version buckets)

### r2_gated_bias, week 1 live (19–27/6, n=1742) — first virgin window
| h | n | MAE | bias | std(z) | IC50 | IC90 |
|---|---|---|---|---|---|---|
| +30 | 873 | 9.9 | −1.2 | 1.65 | 38% | 78% |
| +60 | 869 | 11.7 | −2.3 | 1.60 | 40% | 78% |
| +60 hypo window (<80) | 176 | 17.4 | −17.1 | 1.23 | 18% | 50% |

### r2_gated_bias, week 2 live (28/6–5/7, n=1899) — second virgin window
| h | n | MAE | bias | std(z) | IC90 |
|---|---|---|---|---|---|
| +30 | 953 | 12.4 | +3.7 | 1.83 | 74% |
| +60 | 946 | 15.7 | +5.1 | 1.85 | 74% |
| +60 hypo window | 49 | 26.2 | −16.6 | 1.62 | 35% |

Week-2 bias inflated by the 2/7 under-logged dinner excursion (26 pts >180,
bias +112 among them). Hypo-window over-prediction (−17) stable across weeks.

## Interval calibration (r3: σ×1.64 at +60 — what-if on virgin week 2)
| Metric (+60) | production | with r3 |
|---|---|---|
| IC90 global | 74% | **88%** |
| IC90 hypo window | 35% | **82%** |
| std(z) | 1.85 | **1.13** |
| hypo recall (p_hypo≥0.3) | 32% (6/19) | 32% (6/19) |
| alarms / precision(<80) | 23 / 30% | 28 / 29% |

## r4 (+30 σ×1.68) — held-out 1–5/7 (n=625; tuned on 19–30/6 n=1231)
| Metric (+30) | production | candidate |
|---|---|---|
| IC90 global | 75% | **89%** |
| IC90 hypo window (n=38) | 50% | **71%** |
| std(z) | 1.89 | **1.12** |
| IC90 width | ±15 | ±25 mg/dL |
| hypo recall / alarm precision | 13% / 24% | 13% / **30%** |

## Baseline: zero-order hold on the SAME points (60 d, +60, r2 bucket)
| Regime | n | MAE SSM | MAE naive | winner |
|---|---|---|---|---|
| GLOBAL | 1845 | 13.8 | 13.9 | tie (+1%) |
| Excursions (\|Δ60\|≥25) | 266 | 38.3 | 42.6 | **SSM +10%** |
| Quiet (\|Δ60\|<25) | 1579 | 9.6 | 9.0 | naive (−6%) |
| Hypo window (real<80) | 225 | 19.3 | 17.4 | naive (−11%) |
| **Falls into hypo from ≥100** | 30 | 47.9 | **64.5** | **SSM +26%** |

Naive provides no σ, no p_hypo, no IC, no counterfactual structure.

(r1 bucket vs naive, for the honesty arc: naive won globally by 25–29% before
the r2 bias fix — worth showing as the "why the protocol matters" figure.)

## Open items feeding the discussion
- Hypo-window mean bias −15/−17 at +60 (deliberate: gate off near hypo) —
  next high-risk experiment.
- Input-quality bound: 2/7 case study (late + under-counted dinner).

# Paper outline (working draft)

**Working title:** *Prospective, safety-gated system identification of a personal
glucose forecasting model: an n-of-1 study with continuous live auditing*

**Target venues (in order):** ML4H / CHIL workshop (fast feedback) → Journal of
Diabetes Science and Technology (JDST) → JMIR Diabetes.
**Language:** English. **Code/data:** de-identified audit tables + analysis code
(this repo's `paper/` + `bench/`); raw health data private.

---

## Abstract (sketch, ~150 w)
Most published glucose-forecasting models are evaluated retrospectively on a
single split and never deployed. We describe an n-of-1 system in which a
personal state-space model (UKF, 6 states; meal, insulin, basal and exercise
inputs) runs in production and **every prediction is persisted before the
outcome is known**, then automatically resolved against CGM. Model refinements
follow a pre-specified protocol: tune on train only, validate held-out per
regime, veto on any hypoglycemia-safety regression ("safety gates"), promote
under a new version, and re-validate live. Across five promotions (observation-
noise whitening, safety-gated bias correction, per-horizon interval
calibration), live +60 min bias fell from −10 to ~0 mg/dL and IC90 coverage in
the hypoglycemia window rose from 35% to 82% without regressing hypo recall. A
zero-order-hold baseline ties the model on global MAE — as expected at 94% TIR
— but the model wins during excursions (+10%) and falls into hypoglycemia
(+26%), and uniquely provides calibrated uncertainty. We argue the prospective
audit + safety-gate protocol, not the model, is the transferable contribution.

## 1. Introduction
- CGM forecasting literature: strong retrospective results, weak prospective
  validation; naive baselines rarely reported honestly at short horizons.
- n-of-1 / personal science context (DIY diabetes community, OpenAPS/Loop).
- Contribution: (i) a deployed, continuously self-auditing forecasting pipeline;
  (ii) a pre-specified experiment protocol with hypoglycemia-safety veto;
  (iii) honest evaluation vs naive baseline incl. regime analysis;
  (iv) case evidence that interval calibration is where the clinical value is.

## 2. System
- **Platform:** Flask + SQLite in production (Railway); CGM via LibreLinkUp
  (~5 min); meals/insulin/exercise logged in-app (photo-AI assisted).
- **Model:** UKF over 6-state compartment model, RK4; deterministic inputs:
  bolus, meals (net carbs), basal Toujeo U-300 profile, exercise (direct
  insulin-independent drop + post-exercise sensitivity tail).
- **Audit infrastructure (key novelty):** `PredictionAudit` — every prediction
  row persisted at issue time (μ, σ, IC50/90, p_hypo, filter covariance
  diagnostics), resolved automatically when the CGM reading at t+h arrives.
  Per-version buckets → each model accumulates an untouched track record.
  Git history acts as a timestamped protocol.

## 3. Experiment protocol (the transferable method)
1. Diagnose on the live track record (e.g., innovation ACF, bias by regime).
2. One change at a time; tunables fit on train window ONLY.
3. Held-out validation split by regime (global / in-range / hypo window /
   post-meal / high), with pre-specified gates; **any hypo-safety regression
   vetoes the candidate** regardless of global wins ("safety first").
4. Promote under a new MODEL_VERSION (flag-gated, 1-line rollback).
5. Re-validate live on the next virgin window before the next experiment.
6. Rejected candidates documented (r2-fpe, r2-basal-net).

## 4. Refinement history (results per promotion)
| Ver | Change | Held-out result | Live (virgin) confirmation |
|---|---|---|---|
| ex (H8) | exercise input | Pareto win at +60 | — |
| r1 (H9) | R ×0.30 via innovation whitening | ACF₁ 0.60→0.15; MAE60 17.8→15.4 | live bias −10 revealed → next exp |
| r2 (H10) | glucose-gated net-basal bias offset | 9/9 gates; bias −10.3→~0 | bias −2.3 (wk1), +5.1 (wk2, meal-logging confound) |
| r3 (H11) | σ×1.64 at +60 | IC90 81→92% | virgin wk: IC90 74→88%, hypo 35→82% |
| r4 (H12) | σ×1.68 at +30 | IC90 75→89%, hypo 50→71% | (accumulating) |

## 5. Baselines & regime analysis (honesty section)
- Zero-order hold on the SAME points: global MAE tie (13.8 vs 13.9 at +60).
  State plainly: at 94% TIR, beating naive globally is nearly impossible and
  NOT the point.
- Where the model earns its keep (+60, same points):
  excursions |Δ|≥25: 38.3 vs 42.6 (+10%) · falls into hypo from ≥100:
  47.9 vs 64.5 (+26%) · quiet regime: naive slightly better (9.6 vs 9.0).
- What naive cannot do at all: calibrated σ/IC, p_hypo, counterfactual inputs.
- Secondary baselines: personal AR model, MC/GP pipeline (own audit buckets;
  different windows — report with caveat).

## 6. Case studies
- 2026-07-02 hyper excursion: model under-predicted (+112 bias tail) because
  dinner was logged late and under-counted → forecasting quality is bounded by
  input logging; motivates photo/autocomplete logging UX (brief mention).
- Hypo-window residual bias (−15 to −17 at +60): the deliberate cost of gating
  the bias correction OFF near hypo; candidate for the next (highest-risk)
  experiment.

## 7. Limitations
n=1; high TIR (easy regime); CGM is the ground truth (sensor artifacts
part-handled); meal logging quality varies; no formal pre-registration (git
history as partial substitute); short live windows per version (1-2 wk so far;
longer by submission).

## 8. Ethics & reproducibility
Self-experimentation, observational (no dosing intervention driven by the
model — product layer deliberately excludes predictions). De-identified audit
export + analysis code released.

---

## Figures / tables plan
- **F1** system diagram (data → UKF → prediction → audit → resolve loop)
- **F2** timeline: versions vs live MAE/bias (per-version buckets)
- **F3** reliability diagram (z-coverage) pre/post r3+r4 calibration
- **F4** hypo-window IC90 coverage by version (35→82%)
- **F5** regime bar chart: SSM vs naive (global/excursions/falls-into-hypo)
- **T1** protocol gates per experiment (incl. the two rejected)
- **T2** baselines table (same-points naive; AR/MCGP with caveats)

## TODO before submission
- [ ] 8–12 weeks of r3/r4 live track record (passive accumulation; ~Sep 2026)
- [ ] AR / MC-GP same-window comparison where buckets overlap
- [ ] Reliability diagrams (script: `paper/calibration_figs.py`, pending)
- [ ] De-identified audit export script
- [ ] Draft: intro+methods first (can start now — protocol is stable)

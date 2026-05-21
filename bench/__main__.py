"""
bench CLI
─────────
Uso:
    python -m bench                          # ventana 30d, modelo activo
    python -m bench --days 7
    python -m bench --days 90 --model mc_ar_gp_pmm_v1
    python -m bench --json                   # output JSON puro
    python -m bench --verdict                # solo pass/warn/fail
"""
from __future__ import annotations

import argparse
import json
import sys


def _fmt(v, prec=2):
    if v is None: return "—"
    if isinstance(v, float):
        return f"{v:.{prec}f}"
    return str(v)


def _print_text(report: dict) -> None:
    meta = report["meta"]
    print()
    print(f"╔══════════════════════════════════════════════════════════════╗")
    print(f"║  PMM Backtest Report — {meta['window_days']}d window, run {meta['run_at']}")
    print(f"║  Records totales: {meta['n_records_total']}")
    print(f"║  Modelos: {', '.join(meta['models_compared'])}")
    print(f"╚══════════════════════════════════════════════════════════════╝")

    for model, m in report["by_model"].items():
        print(f"\n━━ Modelo: {model} ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        if "note" in m:
            print(f"  {m['note']}")
            continue
        acc = m["accuracy"]
        print(f"\n  ACCURACY (n={acc['n']})")
        print(f"    MAE    {_fmt(acc['mae'])} mg/dL   "
              f"RMSE   {_fmt(acc['rmse'])}   "
              f"MARD   {_fmt(acc['mard'])}%   "
              f"BIAS   {_fmt(acc['bias'])}")

        for h, hd in m["accuracy_by_horizon"].items():
            print(f"      {h}: MAE {_fmt(hd.get('mae'))}  "
                  f"RMSE {_fmt(hd.get('rmse'))}  "
                  f"MARD {_fmt(hd.get('mard'))}%  "
                  f"(n={hd['n']})")

        print(f"\n  ACCURACY POR CONTEXTO")
        for tag, td in m["accuracy_by_context"].items():
            print(f"    {tag:14s}  n={td['n']:4d}  "
                  f"MAE {_fmt(td.get('mae'), 1):>6s}")

        print(f"\n  ACCURACY POR RANGO GLUCÉMICO")
        for rng, rd in m["accuracy_by_glucose"].items():
            print(f"    {rng:18s}  n={rd['n']:4d}  "
                  f"MAE {_fmt(rd.get('mae'), 1):>6s}  "
                  f"MARD {_fmt(rd.get('mard'), 1):>5s}%")

        for horizon_label, cal in (("+30min", m["calibration_+30min"]),
                                    ("+60min", m["calibration_+60min"])):
            print(f"\n  CALIBRATION {horizon_label}")
            if cal.get("n_with_sigma", 0) == 0:
                note = cal.get("note", "sin datos con σ")
                print(f"    {note}")
                continue
            print(f"    CRPS      {_fmt(cal['crps'])} mg/dL   "
                  f"(menor = mejor)")
            print(f"    ECE       {_fmt(cal['ece'], 4)}   "
                  f"(<0.05 excelente, <0.10 aceptable)")
            print(f"    Sharpness {_fmt(cal['sharpness'])} mg/dL   "
                  f"(σ promedio predictivo)")
            rel = cal["reliability"]
            print(f"    Reliability (quantile → observed):")
            for q, of in zip(rel["quantiles"], rel["observed_freqs"]):
                marker = "✓" if abs(of - q) < 0.05 else ("~" if abs(of - q) < 0.10 else "✗")
                print(f"      q={q:.1f}  obs={of:.2f}  {marker}")

        clin = m["clinical"]
        tir  = clin["tir"]
        print(f"\n  CLINICAL")
        print(f"    TIR observado:  TIR {tir.get('tir_pct')}%   "
              f"hipo {tir.get('hypo_pct')}%   hiper {tir.get('hyper_pct')}%   "
              f"severos: hipo {tir.get('severe_hypo_pct')}% · hiper {tir.get('severe_hyper_pct')}%")

        for h_label, hk in (("+30min", "hypo_+30min"), ("+60min", "hypo_+60min")):
            hd = clin[hk]
            print(f"    Hipo {h_label}:  "
                  f"recall {_fmt(hd.get('recall'))}   "
                  f"precision {_fmt(hd.get('precision'))}   "
                  f"FA/día {_fmt(hd.get('false_alarm_rate_per_day'))}   "
                  f"(reales={hd['n_real_hypos']}, alertas={hd['n_alerts']}, "
                  f"hits={hd['n_true_positives']})")


def _print_verdict(verdict_data: dict) -> None:
    if not verdict_data.get("overall"):
        print(f"❌ {verdict_data.get('error', 'no se pudo evaluar')}")
        return
    icon_overall = {"pass": "✅", "warn": "🟡", "fail": "🔴", "no_data": "⚪"}
    print()
    print(f"  {icon_overall.get(verdict_data['overall'], '?')} "
          f"VERDICT GLOBAL: {verdict_data['overall'].upper()}   "
          f"(modelo: {verdict_data['model']})")
    print()
    icons = {"pass": "✓", "warn": "~", "fail": "✗", "missing": "—"}
    for metric, check in verdict_data["checks"].items():
        target = verdict_data["targets"][metric]
        print(f"    {icons[check['status']]} {metric:24s}  "
              f"valor={_fmt(check['value'])}   target≈{_fmt(target)}   "
              f"[{check['status']}]")


def main() -> int:
    ap = argparse.ArgumentParser(description="PMM backtest runner")
    ap.add_argument("--days",    type=int, default=30, help="ventana en días")
    ap.add_argument("--model",   type=str, default=None, help="model_version a filtrar")
    ap.add_argument("--json",    action="store_true", help="output JSON")
    ap.add_argument("--verdict", action="store_true", help="solo veredicto")
    args = ap.parse_args()

    # Cargar app context para acceder a la DB
    from app import app
    with app.app_context():
        from bench.runner import run_backtest, verdict as _verdict
        report = run_backtest(days=args.days, model_version=args.model)
        if args.verdict:
            v = _verdict(report, model=args.model)
            if args.json:
                print(json.dumps(v, indent=2, default=str))
            else:
                _print_verdict(v)
        elif args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            _print_text(report)
            v = _verdict(report, model=args.model)
            _print_verdict(v)
    return 0


if __name__ == "__main__":
    sys.exit(main())

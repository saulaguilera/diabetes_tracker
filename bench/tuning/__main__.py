"""
bench.tuning CLI
────────────────
Workflow formal de calibration loop.

Comandos:
    python -m bench.tuning grid <name> --param Q_SI=1e-7,5e-7,1e-6 --days 14
    python -m bench.tuning report <name>
    python -m bench.tuning pareto <name> --x ece --y sharpness
    python -m bench.tuning best <name> --top 5
    python -m bench.tuning compare <param_hash_a> <param_hash_b>
"""
from __future__ import annotations

import argparse
import json
import sys


def _fmt(v, prec=3):
    if v is None: return "—"
    if isinstance(v, float): return f"{v:.{prec}f}"
    return str(v)


def cmd_grid(args):
    from bench.tuning.grid_search import ExperimentSpec, run_experiment
    grid = {}
    for spec_str in args.param:
        if "=" not in spec_str:
            print(f"❌ formato inválido: {spec_str} (use NAME=v1,v2,v3)")
            return 1
        name, vals = spec_str.split("=", 1)
        # Parse cada valor como float o int
        parsed = []
        for v in vals.split(","):
            v = v.strip()
            try:    parsed.append(float(v))
            except ValueError:
                try:    parsed.append(int(v))
                except ValueError: parsed.append(v)
        grid[name] = parsed

    spec = ExperimentSpec(
        name=args.name,
        param_grid=grid,
        days=args.days,
        decision_every_min=args.decision_every_min,
        skip_existing=not args.force,
    )

    print(f"╔══ Grid Search: {spec.name} ═══════════════════════════")
    print(f"║  combos: {spec.total_combos()}")
    print(f"║  days:   {spec.days}")
    print(f"║  grid:")
    for k, v in spec.param_grid.items():
        print(f"║    {k}: {v}")
    print(f"╚══════════════════════════════════════════════════════")

    from app import app
    with app.app_context():
        results = run_experiment(spec)

    print(f"\n✅ Completados {len(results)} runs (los ya existentes fueron skipeados)")
    return 0


def cmd_report(args):
    from app import app
    with app.app_context():
        from bench.tuning.grid_search import experiment_summary, load_experiment_results
        summary = experiment_summary(args.name)
        if args.json:
            print(json.dumps(summary, indent=2, default=str))
            return 0
        if summary.get("n_runs", 0) == 0:
            print(f"❌ experimento '{args.name}' sin runs persistidos")
            return 1

        print(f"\n══ Experiment: {summary['name']} ══════════════════════════")
        print(f"  Runs:           {summary['n_runs']} ({summary['n_valid']} válidos)")
        print(f"  Score range:    {summary['score_range']}")
        print(f"  Best composite: {summary['best_composite']}")
        print(f"  Best hash:      {summary['best_hash']}")
        print(f"  Best params:    {json.dumps(summary['best_params'], default=float, indent=2)}")

        results = load_experiment_results(args.name)
        print(f"\n  Top 10 configs por composite:")
        print(f"  {'hash':12s}  {'comp':6s}  {'cal':5s} {'inn':5s} {'cli':5s} {'sta':5s} {'acc':5s}  {'verdict':10s}  notes")
        for r in results[:10]:
            s = r["scores"]
            print(f"  {r['param_hash']:12s}  {_fmt(s['composite'])} "
                  f"  {_fmt(s['calibration'])} {_fmt(s['innovation'])} "
                  f"{_fmt(s['clinical'])} {_fmt(s['stability'])} {_fmt(s['accuracy'])}  "
                  f"{(r['verdict'] or ''):10s}  n={r.get('n_records', '?')}")
    return 0


def cmd_pareto(args):
    from app import app
    with app.app_context():
        from bench.tuning.grid_search import load_experiment_results
        from bench.tuning.pareto       import (
            dominance_filter, filter_acceptable, best_balanced,
            STANDARD_OBJECTIVES, pareto_2d_projection,
        )
        results = load_experiment_results(args.name)
        if not results:
            print(f"❌ experimento '{args.name}' sin runs")
            return 1

        # Adaptar a formato de Pareto
        adapted = []
        for r in results:
            metrics = r.get("metrics", {}).get("flat", {})
            if not metrics: continue
            adapted.append({
                "name":       r["param_hash"],
                "param_hash": r["param_hash"],
                "metrics":    metrics,
                "scores":     r.get("scores", {}),
                "params":     r.get("params", {}),
            })

        if args.acceptable:
            acceptable = filter_acceptable(adapted)
            print(f"\n  Configs aceptables: {len(acceptable)} / {len(adapted)}")
            adapted = acceptable

        frontier = dominance_filter(adapted)
        best = best_balanced(frontier)

        print(f"\n══ Pareto Frontier: {args.name} ══════════════════════════")
        print(f"  Total configs:   {len(adapted)}")
        print(f"  Frontier size:   {len(frontier)}")
        print(f"\n  Frontier configs (no-dominados):")
        print(f"  {'hash':12s}  {'comp':6s}  {'mae30':6s} {'ece':6s} {'ic90':6s} {'recall':6s}")
        for c in frontier:
            m = c["metrics"]; s = c["scores"]
            print(f"  {c['param_hash']:12s}  {_fmt(s.get('composite'))} "
                  f"  {_fmt(m.get('mae_30'),1)} {_fmt(m.get('ece'))} "
                  f"{_fmt(m.get('ic90_coverage'))} {_fmt(m.get('hypo_recall_30'))}")

        if best:
            print(f"\n  ★ Best balanced (closest to ideal):")
            print(f"     hash:      {best['param_hash']}")
            print(f"     composite: {best['scores'].get('composite')}")
            print(f"     params override:")
            for k, v in (best.get("params") or {}).items():
                print(f"        {k} = {v}")

        if args.x and args.y:
            proj = pareto_2d_projection(adapted, args.x, args.y,
                                         x_maximize=args.x_max,
                                         y_maximize=args.y_max)
            print(f"\n  2D projection ({args.x} vs {args.y}):")
            print(f"  {'label':14s} {args.x:>10s} {args.y:>10s}  frontier?")
            for p in proj["all"]:
                mark = "  ✓" if p["frontier"] else ""
                print(f"  {(p['label'] or '?'):14s} {_fmt(p['x']):>10s} {_fmt(p['y']):>10s}{mark}")
    return 0


def cmd_best(args):
    from app import app
    with app.app_context():
        from bench.tuning.grid_search import best_configs
        rows = best_configs(args.name, top_k=args.top)
        if args.json:
            print(json.dumps(rows, indent=2, default=str))
            return 0
        print(f"\n══ Top {len(rows)} configs for '{args.name}' ══════════════")
        for i, r in enumerate(rows, 1):
            s = r["scores"]
            print(f"\n  #{i}  hash={r['param_hash']}  composite={s.get('composite')}")
            print(f"     cal={s.get('calibration')} inn={s.get('innovation')} "
                  f"cli={s.get('clinical')} sta={s.get('stability')} acc={s.get('accuracy')}")
            print(f"     params:")
            for k, v in (r.get("params") or {}).items():
                if v != getattr(__import__("pmm.ssm.parameters", fromlist=["SSMParameters"]).SSMParameters(), k, None):
                    print(f"        {k} = {v}  (override)")
    return 0


def cmd_diagnostics(args):
    """Deep diagnostics sobre el SSM real (no replay-based)."""
    from app import app
    with app.app_context():
        from bench.metrics.innovations    import load_innovations
        from bench.tuning.deep_diagnostics import deep_innovation_analysis

        inns = load_innovations(days=args.days, model_version=args.model)
        if not inns:
            print(f"❌ sin innovations cargadas (días={args.days}, model={args.model})")
            return 1
        analysis = deep_innovation_analysis(inns)
        if args.json:
            print(json.dumps(analysis, indent=2, default=str))
            return 0

        print(f"\n══ Deep Innovation Analysis ({analysis['n']} samples) ══════")
        mo = analysis["moments"]
        print(f"\n  Moments:")
        print(f"    mean={mo['mean']}  var={mo['var']}  skew={mo['skew']}  kurt={mo['kurt']} (excess)")
        jb = analysis["jarque_bera"]
        print(f"\n  Jarque-Bera: JB={jb.get('JB')}  p={jb.get('p_value')}  "
              f"normal={jb.get('normal')}")
        ht = analysis["heavy_tails"]
        print(f"\n  Heavy tails (>3σ):")
        print(f"    outliers: {ht['n_outliers']} / esperados {ht['expected']} (ratio {ht['ratio']})")
        print(f"    heavy_tailed: {ht['heavy_tailed']}")

        rv = analysis["rolling_var"]
        if rv:
            print(f"\n  Rolling var (window): first={rv[0]:.3f}  last={rv[-1]:.3f}  range=[{min(rv):.3f}, {max(rv):.3f}]")

        if "by_regime" in analysis and isinstance(analysis["by_regime"], dict):
            print(f"\n  By regime:")
            for regime, st in analysis["by_regime"].items():
                if st.get("n", 0) < 5: continue
                print(f"    {regime:12s} n={st['n']:4d}  μ={st.get('mean'):.3f}  "
                      f"σ²={st.get('var'):.3f}  k={st.get('kurt'):.2f}")
                print(f"                  {st.get('verdict', '')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="bench.tuning",
                                  description="SSM hyperparameter tuning framework")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("grid", help="Run grid search experiment")
    sp.add_argument("name")
    sp.add_argument("--param", action="append", default=[],
                    help="PARAM=v1,v2,v3 (repetible)")
    sp.add_argument("--days", type=int, default=14)
    sp.add_argument("--decision-every-min", type=int, default=30)
    sp.add_argument("--force", action="store_true", help="Re-evaluar combos existentes")
    sp.set_defaults(func=cmd_grid)

    sp = sub.add_parser("report", help="Resumen de un experimento")
    sp.add_argument("name")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("pareto", help="Pareto frontier analysis")
    sp.add_argument("name")
    sp.add_argument("--acceptable", action="store_true",
                    help="Filtrar configs no aceptables antes del frontier")
    sp.add_argument("--x", help="Métrica X para proyección 2D")
    sp.add_argument("--y", help="Métrica Y para proyección 2D")
    sp.add_argument("--x-max", action="store_true", help="X es maximize")
    sp.add_argument("--y-max", action="store_true", help="Y es maximize")
    sp.set_defaults(func=cmd_pareto)

    sp = sub.add_parser("best", help="Top-K configs por composite")
    sp.add_argument("name")
    sp.add_argument("--top", type=int, default=5)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_best)

    sp = sub.add_parser("diagnostics", help="Deep innovation diagnostics")
    sp.add_argument("--days",  type=int, default=14)
    sp.add_argument("--model", default="ssm_v0_ukf6")
    sp.add_argument("--json",  action="store_true")
    sp.set_defaults(func=cmd_diagnostics)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

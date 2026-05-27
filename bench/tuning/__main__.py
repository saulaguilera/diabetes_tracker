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
        parent_name=getattr(args, "parent", None),
    )

    rt = spec.estimated_runtime()
    invalid = spec.validate_combinations()

    print(f"╔══ Grid Search: {spec.name} ═══════════════════════════")
    print(f"║  combos:        {spec.total_combos()}")
    print(f"║  days:          {spec.days}")
    if spec.parent_name:
        print(f"║  parent:        {spec.parent_name}")
    print(f"║  estimated:     {rt['total_str']}")
    if invalid:
        print(f"║  ⚠ {len(invalid)} combos with physical warnings (will be skipped)")
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


def cmd_protocols(args):
    """Lista los baseline protocols disponibles con su runtime estimado."""
    from app import app
    with app.app_context():
        from bench.tuning.protocol import list_protocols, PROTOCOLS, suggest_range
        if args.show:
            p = PROTOCOLS.get(args.show)
            if not p:
                print(f"❌ protocolo '{args.show}' no existe")
                return 1
            print(f"\n══ {p.name} ════════════════════════════════════════")
            print(f"  Rationale:    {p.rationale}")
            print(f"  Days:         {p.days}")
            print(f"  Combos:       {p.estimated_runtime()['n_combos']}")
            print(f"  Est. runtime: {p.estimated_runtime()['total_str']}")
            print(f"  Param grid:")
            for k, vals in p.param_grid.items():
                print(f"    {k}: {vals}")
            return 0
        if args.suggest:
            r = suggest_range(args.suggest)
            if not r:
                print(f"❌ param '{args.suggest}' sin range sugerido")
                return 1
            print(f"Sugerencia para {args.suggest}: {r}")
            return 0
        # Listar todos
        print(f"\n══ Baseline Protocols ══════════════════════════════════")
        for p in list_protocols():
            print(f"  • {p['name']:25s}  combos={p['n_combos']:3d}  "
                  f"est={p['estimated_time']}")
            print(f"    {p['rationale'][:75]}{'...' if len(p['rationale']) > 75 else ''}")
    return 0


def cmd_gates(args):
    """Evalúa los 8 promotion gates sobre el SSM en vivo."""
    from app import app
    with app.app_context():
        from bench.tuning.promotion_gates import (
            compute_gate_metrics, evaluate_gates,
            gates_rolling_history, stability_summary,
        )
        metrics = compute_gate_metrics(days=args.days, model_version=args.model)
        ev = evaluate_gates(metrics)
        if args.json:
            print(json.dumps({"current": ev, "metrics": metrics}, indent=2, default=str))
            return 0

        v = ev["verdict"]
        icon = "✅" if v == "ready" else "🟡" if v == "near_ready" else "🔴"
        print(f"\n══ Promotion Gates ({args.model}, {args.days}d window) ══")
        print(f"  {icon} {ev['n_passed']}/{ev['n_total']} gates passed")
        print(f"     promotion_readiness: {ev['promotion_readiness']}")
        print(f"     verdict: {v}")
        print()
        for g in ev["gates"]:
            mark = "✓" if g["passed"] else "✗"
            print(f"    {mark} {g['name']:24s} value={_fmt(g['value'])}  "
                  f"target {g['target']:14s}  {g['note'][:50]}")
        if ev["blockers"]:
            print(f"\n  Blockers: {', '.join(ev['blockers'])}")

        # Rolling history
        if args.rolling:
            history = gates_rolling_history(days=args.rolling, window_days=7,
                                             model_version=args.model)
            stab = stability_summary(history)
            print(f"\n══ Rolling {args.rolling}d history (window=7d) ══")
            for h in history:
                if h.get("readiness") is None:
                    print(f"  {h['day']}  —  {h.get('note', 'n/a')}")
                else:
                    print(f"  {h['day']}  {h['n_passed']}/{h.get('n_total', 8)}  "
                          f"readiness={h['readiness']:.2f}  {h['verdict']}")
            if stab.get("n_days", 0) > 0:
                print(f"\n  Stability:")
                print(f"    mean readiness:  {stab['mean_readiness']}")
                print(f"    stdev:           {stab['stdev_readiness']}")
                print(f"    max streak ready: {stab['max_streak_ready']}d")
                if stab.get("most_blocking"):
                    print(f"    most blocking:   {stab['most_blocking'][0]} "
                          f"({stab['most_blocking'][1]} days)")
    return 0


def cmd_attribute(args):
    """Genera failure attribution + suggested sweep para un experiment."""
    from app import app
    with app.app_context():
        from bench.tuning.grid_search import load_experiment_results
        from bench.tuning.attribution import diagnose, suggested_next_sweep
        rows = load_experiment_results(args.name)
        if not rows:
            print(f"❌ sin runs para '{args.name}'")
            return 1
        best = rows[0]
        flat = (best.get("metrics") or {}).get("flat", {})
        diagnoses = diagnose(flat)
        if args.json:
            print(json.dumps({"best": best, "diagnoses": diagnoses,
                              "suggested_sweep": suggested_next_sweep(diagnoses)},
                              indent=2, default=str))
            return 0
        print(f"\n══ Failure Attribution: {args.name} (best={best['param_hash']}) ══")
        if not diagnoses:
            print(f"  ✅ No issues detectados — el best config cumple los criterios principales")
            return 0
        print(f"  {len(diagnoses)} diagnoses ranked por confidence:\n")
        for d in diagnoses[:5]:
            print(f"  [{d['confidence']:.2f}] {d['category']:12s}  {d['rule']}")
            print(f"        {d['hypothesis']}")
            print(f"        params implicados: {', '.join(d['params'])}")
            print(f"        sweep hint: {d['sweep_hint']}")
            print()
        sweep = suggested_next_sweep(diagnoses)
        if sweep:
            print(f"  Suggested next sweep:")
            cmd_str = " ".join(f"--param {k}={','.join(str(v) for v in vs)}"
                               for k, vs in sweep.items())
            print(f"    python -m bench.tuning grid {args.name}_v2 {cmd_str} --days {args.days}")
    return 0


def cmd_sensitivity(args):
    """Análisis de sensibilidad sobre experiments persistidos."""
    from app import app
    with app.app_context():
        from bench.tuning.sensitivity import (
            local_sensitivity, parameter_importance,
            suggest_dimensionality_reduction,
        )
        local = local_sensitivity(args.name)
        importance = parameter_importance(args.name)
        reduction  = suggest_dimensionality_reduction(args.name)
        if args.json:
            print(json.dumps({"local": local, "importance": importance,
                              "reduction": reduction}, indent=2, default=str))
            return 0

        print(f"\n══ Sensitivity Analysis: {args.name} ══════════════════════")
        if "ranking" in importance:
            print(f"\n  Parameter importance ranking (Sobol-light):")
            for rank, p in enumerate(importance["ranking"], 1):
                info = importance["importance"][p]
                Si = info.get("S_i")
                star = "★★★" if (Si or 0) > 0.3 else "★★" if (Si or 0) > 0.1 else "★"
                print(f"    {rank}. {p:22s}  S_i={_fmt(Si)}  {star}  "
                      f"best→{info.get('best_value')} worst→{info.get('worst_value')}")
        if reduction.get("dominant"):
            print(f"\n  Dominant params (focus tuning here):")
            for p, S in reduction["dominant"]:
                print(f"    {p:22s}  S_i={_fmt(S)}")
        if reduction.get("irrelevant"):
            print(f"\n  Low-impact params (consider fixing in next sweep):")
            for p, S in reduction["irrelevant"]:
                print(f"    {p:22s}  S_i={_fmt(S)}")
    return 0


def cmd_regimes(args):
    """Regime-specific breakdown."""
    from app import app
    with app.app_context():
        from bench.tuning.regimes import evaluate_regimes, regime_specific_diagnoses
        ev = evaluate_regimes(days=args.days, model_version=args.model,
                              horizon_min=args.horizon)
        diags = regime_specific_diagnoses(ev)
        if args.json:
            print(json.dumps({"regimes": ev, "diagnoses": diags}, indent=2, default=str))
            return 0
        if ev.get("n", -1) == 0 or not ev.get("regimes"):
            print(f"❌ sin audits resueltos en este horizon")
            return 1
        print(f"\n══ Regime Analysis: +{args.horizon}min, {args.days}d ══════════")
        print(f"  Total audits: {ev['n_total']}")
        if ev.get("worst_regime"):
            print(f"  ⚠ Worst regime: {ev['worst_regime']}")
        print()
        for regime, st in ev["regimes"].items():
            n = st.get("n", 0)
            if n < 3:
                print(f"  {regime:14s} n={n}  (insufficient)")
                continue
            print(f"  {regime:14s} n={n:4d}  MAE={_fmt(st.get('mae'),1):>6s}  "
                  f"IC50={_fmt(st.get('ic50_coverage'),2):>5s}  "
                  f"IC90={_fmt(st.get('ic90_coverage'),2):>5s}  "
                  f"var_z={_fmt(st.get('var_z'),2):>5s}")
            v = st.get("verdict", "")
            if v != "OK":
                print(f"                 → {v}")
        if diags:
            print(f"\n  Regime-specific diagnoses ({len(diags)}):")
            for d in diags[:3]:
                print(f"    [{d['confidence']:.2f}] {d['regime']:10s}: {d['hypothesis'][:70]}...")
    return 0


def cmd_lineage(args):
    """Lineage graph y comparison."""
    from app import app
    with app.app_context():
        from bench.tuning.lineage import (
            build_lineage, lineage_path, impact_analysis, latest_branch_summary,
        )
        if args.compare:
            parent, child = args.compare.split(":")
            result = impact_analysis(parent, child)
            if args.json:
                print(json.dumps(result, indent=2, default=str))
                return 0
            if not result.get("ok"):
                print(f"❌ {result.get('error')}")
                return 1
            print(f"\n══ Impact: {parent} → {child} ══════════════════════")
            print(f"  Verdict: {result['verdict']}")
            print(f"  Summary: {result['summary']}")
            print(f"\n  Deltas:")
            for k, v in result["deltas"].items():
                arrow = "↑" if v > 0 else "↓" if v < 0 else "·"
                print(f"    {k:18s}  {v:+.4f} {arrow}")
            if result.get("param_changes"):
                print(f"\n  Param changes:")
                for k, ch in result["param_changes"].items():
                    print(f"    {k:22s}  {ch['from']} → {ch['to']}")
            return 0
        if args.name:
            if args.path:
                path = lineage_path(args.name)
                if args.json:
                    print(json.dumps(path, indent=2, default=str)); return 0
                print(f"\n══ Lineage path to '{args.name}' ══")
                for n in path:
                    print(f"  ↳ {n['name']:25s} composite={_fmt(n['score_composite'])}  "
                          f"gates={n.get('gates_passed')}/8")
                return 0
            tree = build_lineage(args.name)
            if args.json:
                print(json.dumps(tree, indent=2, default=str)); return 0
            _print_tree(tree, indent=0)
            return 0
        # Default: latest
        rows = latest_branch_summary()
        if args.json:
            print(json.dumps(rows, indent=2, default=str)); return 0
        print(f"\n══ Latest experiments ══════════════════════════════════")
        for r in rows:
            arrow = (f"↑{r['Δcomposite']:+.3f}" if r.get('Δcomposite') is not None else "")
            print(f"  {r['name']:28s}  best={_fmt(r['best_score'])}  "
                  f"parent={r.get('parent') or '—':18s}  {arrow}")
    return 0


def _print_tree(node, indent: int):
    if node.get("missing"):
        print("  " * indent + f"⊘ {node['name']}  (missing)")
        return
    best = node.get("best") or {}
    verdict = node.get("verdict", "")
    icon = {"improvement": "✓", "marginal_gain": "·", "no_change": " ",
            "regression": "✗", "minor_regression": "~", "root": "▸"}.get(verdict, " ")
    line = f"{icon} {node['name']:28s}  composite={_fmt(best.get('score_composite'))}"
    if node.get("delta_vs_parent") and node["delta_vs_parent"].get("Δcomposite") is not None:
        line += f"  Δ={node['delta_vs_parent']['Δcomposite']:+.3f}"
    print("  " * indent + line)
    for child in node.get("children", []):
        _print_tree(child, indent + 1)


def cmd_repro(args):
    """Determinism assertion + verification."""
    from app import app
    with app.app_context():
        from bench.tuning.reproducibility import (
            assert_deterministic, verify_experiment, data_checksum,
        )
        from pmm.ssm.parameters import SSMParameters
        from datetime import datetime as _dt
        if args.verify_id:
            r = verify_experiment(args.verify_id)
            print(f"  ok={r.get('ok')}")
            print(f"  expected={r.get('expected')}  actual={r.get('actual')}")
            return 0 if r.get("ok") else 1
        # Determinism: corrida doble
        params = SSMParameters()
        result = assert_deterministic(name="repro_test", params=params,
                                       days=args.days, runs=args.runs)
        if args.json:
            print(json.dumps(result, indent=2, default=str)); return 0
        print(f"\n══ Reproducibility Test ══════════════════════════════")
        print(f"  Runs: {args.runs}")
        print(f"  Days window: {args.days}")
        print(f"  Data checksum: {data_checksum(_dt.now(), args.days)}")
        print(f"  Records per run: {result['n_records']}")
        print(f"  Checksums:")
        for i, cs in enumerate(result['checksums']):
            print(f"    run #{i+1}: {cs}")
        print(f"\n  {result['note']}")
    return 0 if result.get('deterministic') else 1


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
    sp.add_argument("--parent", default=None, help="parent experiment (lineage)")
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
    sp.add_argument("--model", default="ssm_v0_ukf6_basal")
    sp.add_argument("--json",  action="store_true")
    sp.set_defaults(func=cmd_diagnostics)

    sp = sub.add_parser("protocols", help="Baseline protocols (sugeridos)")
    sp.add_argument("--show",    help="ver detalle de protocolo")
    sp.add_argument("--suggest", help="rango sugerido para un param")
    sp.set_defaults(func=cmd_protocols)

    sp = sub.add_parser("gates", help="Promotion gates evaluation")
    sp.add_argument("--days",    type=int, default=7)
    sp.add_argument("--rolling", type=int, default=0,
                    help="evaluar también rolling N días")
    sp.add_argument("--model",   default="ssm_v0_ukf6_basal")
    sp.add_argument("--json",    action="store_true")
    sp.set_defaults(func=cmd_gates)

    sp = sub.add_parser("attribute", help="Failure attribution sobre best run")
    sp.add_argument("name")
    sp.add_argument("--days", type=int, default=7)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_attribute)

    sp = sub.add_parser("sensitivity", help="Parameter importance ranking")
    sp.add_argument("name")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_sensitivity)

    sp = sub.add_parser("regimes", help="Regime-specific failure analysis")
    sp.add_argument("--days",    type=int, default=14)
    sp.add_argument("--horizon", type=int, default=30)
    sp.add_argument("--model",   default="ssm_v0_ukf6_basal")
    sp.add_argument("--json",    action="store_true")
    sp.set_defaults(func=cmd_regimes)

    sp = sub.add_parser("lineage", help="Lineage graph / comparison")
    sp.add_argument("name", nargs="?", default=None,
                    help="root experiment para ver tree")
    sp.add_argument("--path",   action="store_true",
                    help="solo el path linear ancestros→self")
    sp.add_argument("--compare", default=None,
                    help="parent:child — impact analysis")
    sp.add_argument("--json",   action="store_true")
    sp.set_defaults(func=cmd_lineage)

    sp = sub.add_parser("repro", help="Reproducibility check")
    sp.add_argument("--days",      type=int, default=3)
    sp.add_argument("--runs",      type=int, default=2)
    sp.add_argument("--verify-id", type=int, default=None,
                    help="verify a specific TuningExperiment id")
    sp.add_argument("--json",      action="store_true")
    sp.set_defaults(func=cmd_repro)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

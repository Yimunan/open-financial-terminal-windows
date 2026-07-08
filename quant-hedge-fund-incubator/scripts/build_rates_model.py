"""Build the rates term-structure models on the Treasury curve, then train + version a curve
forecaster in the taxonomy-aware ModelRepository.

Shows: PCA level/slope/curvature (+ explained variance), Nelson-Siegel parametric fit (+ RMSE),
carry/roll-down of the 10Y, then trains a Ridge + GBR forecaster of the 21-day-ahead 10Y change
and saves them under  models/curve/rates/<name>/  (mirroring the data lake's partitioning).

  .venv\\Scripts\\python.exe scripts\\build_rates_model.py
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.models import ModelRepository, ModelStage
from qhfi.models.predictive import ModelSpec
from qhfi.rates.curve import carry_rolldown, curve_metrics, load_treasury_curve
from qhfi.rates.forecast import train_and_save_curve_forecaster, train_curve_forecaster
from qhfi.rates.nelson_siegel import NelsonSiegel
from qhfi.rates.pca import CurvePCA

TARGET, HORIZON = "10Y", 21


def main() -> None:
    curve = load_treasury_curve()
    print(f"Treasury curve: {curve.shape[0]:,} days × {list(curve.columns)} "
          f"({curve.index.min().date()} → {curve.index.max().date()})")
    latest = curve.iloc[-1]
    print("Latest (%): " + "  ".join(f"{t}={latest[t]:.2f}" for t in curve.columns))

    # 1. PCA level / slope / curvature
    pca = CurvePCA().fit(curve)
    ev = pca.explained()
    print(f"\nPCA of curve changes — explained variance: "
          + "  ".join(f"{n}={v:.1%}" for n, v in ev.items()) + f"  (cum {ev.sum():.1%})")
    print("Loadings:\n" + pca.loadings().round(3).to_string())

    # 2. Nelson-Siegel parametric fit
    ns = NelsonSiegel().fit(curve)
    betas = ns.factors(curve)
    print(f"\nNelson-Siegel (lam={ns.lam}) RMSE across curve: {ns.rmse(curve):.3f}%  "
          f"latest β: level={betas.iloc[-1]['level']:.2f} slope={betas.iloc[-1]['slope']:.2f} "
          f"curv={betas.iloc[-1]['curvature']:.2f}")

    # 3. carry / roll-down snapshot
    cr = carry_rolldown(curve, TARGET, HORIZON).iloc[-1]
    m = curve_metrics(curve).iloc[-1]
    print(f"\n{TARGET} carry+roll ({HORIZON}d): carry={cr['carry']:.3f}% roll={cr['rolldown']:.3f}% "
          f"total={cr['carry_roll']:.3f}%   |  level={m['level']:.2f} slope={m['slope']:.2f} "
          f"curv={m['curvature']:.2f}")

    # 4. forecaster — predict the 21-day-ahead 10Y yield change
    repo = ModelRepository()
    specs = {"ridge": ModelSpec(kind="ridge", params={"alpha": 1.0}),
             "gbr": ModelSpec(kind="gbr", params={"n_estimators": 150, "max_depth": 2,
                                                  "learning_rate": 0.03, "subsample": 0.7})}
    print(f"\nForecasting Δ{TARGET} {HORIZON}d ahead from curve factors:")
    print(f"{'model':<8} {'R2':>7} {'IC':>7} {'n':>7}")
    best: tuple[str, float, int] | None = None
    for name, spec in specs.items():
        _, metrics, _ = train_curve_forecaster(curve, spec, target=TARGET, horizon=HORIZON)
        card = train_and_save_curve_forecaster(repo, f"ust-{TARGET.lower()}-{name}", curve, spec,
                                               target=TARGET, horizon=HORIZON)
        print(f"{name:<8} {metrics['r2']:>7.3f} {metrics['ic']:>7.3f} {metrics['n']:>7.0f}")
        if best is None or metrics["ic"] > best[1]:
            best = (card.name, metrics["ic"], card.version)

    if best is not None:
        repo.promote(best[0], best[2], ModelStage.PRODUCTION)
        print(f"\nPromoted {best[0]} v{best[2]} → PRODUCTION (best IC {best[1]:.3f}).")
        prod = repo.production(best[0])
        if prod is not None:
            c = prod[1]
            print(f"Stored at models/{c.domain.value}/{c.asset_class.value}/{c.name}/v{c.version}/ "
                  f"(domain={c.domain.value}, asset_class={c.asset_class.value}).")

    print("\nNote: a directional 21d yield-change forecast from 4 tenors of daily curve data is a "
          "demo of the modeling layer, not a validated rates signal — pull the full 11-tenor FRED "
          "curve and add walk-forward OOS before trusting any IC.")


if __name__ == "__main__":
    main()

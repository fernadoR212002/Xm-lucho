"""Validación del modelo base contra el despacho ideal publicado por XM.

El despacho ideal de XM es uninodal, sin red y sin reserva AGC: es el comparable directo del
modelo. Se comparan:
  * precio marginal horario  vs MaxPrecOferNal (y precio de bolsa vs PrecBolsNaci)
  * energía diaria por recurso y por tecnología vs GeneIdea
  * recurso marginal vs IndRecMargina
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .modelo import Escenario, Parametros, Resultado, despachar
from .preparacion import Insumos


def correr_validacion(ins: Insumos, par: Parametros, umbrales: dict) -> tuple[Resultado, dict, pd.DataFrame]:
    res = despachar(ins, Escenario("validacion", reserva=False), par)
    m = metricas(res)
    m["veredicto"] = ("ACEPTADO" if m["mape_precio"] <= umbrales["mape_precio_max"]
                      and m["wape_energia_diaria"] <= umbrales["wape_energia_diaria_max"]
                      else "AJUSTAR SUPUESTOS")
    return res, m, tabla_tecnologias(res)


def metricas(res: Resultado) -> dict:
    ins = res.ins
    ref = ins.precio_marginal_xm
    ok = np.isfinite(ref) & (ref > 0)
    err = res.precio[ok] - ref[ok]
    gi, g = ins.gen_ideal, res.g
    dias = ins.dias
    e_ref = gi.reshape(ins.R, dias, 24).sum(axis=2)
    e_mod = g.reshape(ins.R, dias, 24).sum(axis=2)
    con_marginal = ins.marginal_xm != ""
    return {
        "horas": int(ins.P),
        "mae_precio": float(np.abs(err).mean()),
        "mape_precio": float(np.abs(err / ref[ok]).mean()),
        "sesgo_precio": float(err.mean()),
        "correlacion_precio": float(np.corrcoef(res.precio[ok], ref[ok])[0, 1]),
        "horas_precio_exacto_pct": float((np.abs(err) < 0.5).mean()),
        "precio_medio_modelo": float(res.precio[ok].mean()),
        "precio_medio_xm": float(ref[ok].mean()),
        "bolsa_media_modelo": float(res.precio_bolsa[ok].mean()),
        "bolsa_media_xm": float(ins.precio_bolsa_xm[ok].mean()),
        "wape_horario": float(np.abs(g - gi).sum() / gi.sum()),
        "wape_energia_diaria": float(np.abs(e_mod - e_ref).sum() / e_ref.sum()),
        "recurso_marginal_coincide_pct": float((res.marginal[con_marginal] == ins.marginal_xm[con_marginal]).mean())
        if con_marginal.any() else float("nan"),
        "deficit_MWh": float(res.deficit.sum()),
    }


def tabla_tecnologias(res: Resultado) -> pd.DataFrame:
    ins = res.ins
    tipo = ins.recursos["tipo"].to_numpy()
    mod = pd.Series(res.g.sum(axis=1), index=tipo).groupby(level=0).sum()
    ref = pd.Series(ins.gen_ideal.sum(axis=1), index=tipo).groupby(level=0).sum()
    t = pd.DataFrame({"modelo_GWh": mod / 1e3, "xm_GeneIdea_GWh": ref / 1e3})
    t["diferencia_GWh"] = t["modelo_GWh"] - t["xm_GeneIdea_GWh"]
    t["diferencia_pct"] = t["diferencia_GWh"] / t["xm_GeneIdea_GWh"]
    return t.sort_values("xm_GeneIdea_GWh", ascending=False)


def tabla_recursos(res: Resultado, n: int = 15) -> pd.DataFrame:
    """Recursos con mayor diferencia absoluta de energía en el periodo."""
    ins = res.ins
    t = ins.recursos[["nombre", "tipo"]].copy()
    t["modelo_GWh"] = res.g.sum(axis=1) / 1e3
    t["xm_GWh"] = ins.gen_ideal.sum(axis=1) / 1e3
    t["oferta_media_COP_kWh"] = np.where(ins.disp.sum(axis=1) > 0,
                                         (ins.costo * (ins.disp > 0)).sum(axis=1) / np.maximum((ins.disp > 0).sum(axis=1), 1), np.nan)
    t["diferencia_GWh"] = t["modelo_GWh"] - t["xm_GWh"]
    return t.reindex(t["diferencia_GWh"].abs().sort_values(ascending=False).index).head(n)

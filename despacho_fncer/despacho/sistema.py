"""Comportamiento real del SIN en el periodo: demanda, generación por categoría, solar del
mediodía, rampas, precio e inercia estimada. Todo sale de datos medidos por XM (Gene, DemaCome,
PrecBolsNaci), no del modelo.

Definiciones:
  demanda neta      = demanda − solar (toda) − eólica. Es la que deben cubrir las fuentes
                      sincrónicas y gestionables; su forma al mediodía es la "curva de pato".
  residual central  = demanda − todo lo que no oferta precio (solar/hidráulica menor, AGPE, GD,
                      cogeneración...). Es lo que el modelo despacha por orden de mérito.
  no sincrónica     = (solar + eólica) / demanda (SNSP, por sus siglas en inglés).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .datos import FuenteXM
from .preparacion import Insumos

SOLAR_NO_DESPACHADA = ["Solar AGPE", "Solar GD", "Solar planta menor", "Solar autogenerador"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# Agrupación para gráficos: pocas categorías legibles.
GRUPOS = {
    "Hidráulica": ["Hidráulica despachada", "Hidráulica no despachada"],
    "Térmica": ["Térmica"],
    "Otros sincrónicos": ["Cogeneración", "Otros"],
    "Eólica": ["Eólica"],
    "Solar despachada": ["Solar despachada"],
    "Solar no despachada": SOLAR_NO_DESPACHADA,
}


def _col(df: pd.DataFrame, cols) -> pd.Series:
    return df.reindex(columns=cols).fillna(0.0).sum(axis=1)


def serie_real(ins: Insumos) -> pd.DataFrame:
    """Serie horaria real del SIN (P filas)."""
    r = ins.real
    df = pd.DataFrame(index=ins.tiempo)
    df.index.name = "fecha_hora"
    df["demanda_MW"] = ins.demanda
    for grupo, cats in GRUPOS.items():
        df[f"{grupo}_MW"] = _col(r, cats).to_numpy()
    for c in SOLAR_NO_DESPACHADA:
        df[f"{c}_MW"] = _col(r, [c]).to_numpy()
    df["solar_total_MW"] = df["Solar despachada_MW"] + df["Solar no despachada_MW"]
    df["demanda_neta_MW"] = df["demanda_MW"] - df["solar_total_MW"] - df["Eólica_MW"]
    df["residual_central_MW"] = ins.residual()
    df["solar_pct_demanda"] = 100 * df["solar_total_MW"] / df["demanda_MW"]
    df["no_sincronica_pct"] = 100 * (df["solar_total_MW"] + df["Eólica_MW"]) / df["demanda_MW"]
    df["precio_bolsa_COP_kWh"] = ins.precio_bolsa_xm
    if ins.inercia_real is not None:
        df["inercia_GWs"] = ins.inercia_real
        df["H_sistema_s"] = ins.inercia_real / (ins.demanda / 1e3)
    return df


def perfil_horario(serie: pd.DataFrame) -> pd.DataFrame:
    """Promedio por hora del día (hora XM 1..24: la hora 13 va de 12:00 a 13:00)."""
    p = serie.groupby(serie.index.hour + 1).mean(numeric_only=True)
    p.index.name = "hora"
    return p


def resumen_diario(serie: pd.DataFrame) -> pd.DataFrame:
    s = serie
    g = s.groupby(s.index.normalize())
    hora = lambda col, f: g[col].agg(f).map(lambda t: t.hour + 1)  # noqa: E731
    neta = s["demanda_neta_MW"]
    rampa = neta.diff().where(neta.index.hour != 0)  # no mezclar la hora 24 con la 1 del día siguiente
    d = pd.DataFrame({
        "dia": [DIAS[x.weekday()] for x in g.size().index],
        "demanda_GWh": g["demanda_MW"].sum() / 1e3,
        "demanda_max_MW": g["demanda_MW"].max(),
        "hora_demanda_max": hora("demanda_MW", "idxmax"),
        "demanda_min_MW": g["demanda_MW"].min(),
        "solar_GWh": g["solar_total_MW"].sum() / 1e3,
        "solar_no_despachada_GWh": g["Solar no despachada_MW"].sum() / 1e3,
        "solar_max_pct_demanda": g["solar_pct_demanda"].max(),
        "hora_solar_max": hora("solar_total_MW", "idxmax"),
        "demanda_neta_min_MW": g["demanda_neta_MW"].min(),
        "hora_demanda_neta_min": hora("demanda_neta_MW", "idxmin"),
        "rampa_max_demanda_neta_MW_h": rampa.groupby(rampa.index.normalize()).max(),
        "precio_bolsa_medio_COP_kWh": g["precio_bolsa_COP_kWh"].mean(),
        "precio_bolsa_max_COP_kWh": g["precio_bolsa_COP_kWh"].max(),
    })
    if "inercia_GWs" in s:
        d["inercia_min_GWs"] = g["inercia_GWs"].min()
        d["hora_inercia_min"] = hora("inercia_GWs", "idxmin")
    d.index.name = "fecha"
    d.index = d.index.date
    return d


def desglose_solar(ins: Insumos, serie: pd.DataFrame) -> pd.DataFrame:
    """Solar por categoría: energía, pico, aporte medio a las 12-13 h."""
    filas = []
    a_mediodia = serie.index.hour == 12
    for c in ["Solar despachada", *SOLAR_NO_DESPACHADA]:
        x = serie[f"{c}_MW"]
        filas.append({"categoria": c, "energia_GWh": x.sum() / 1e3, "pico_MW": x.max(),
                      "media_12_13h_MW": x[a_mediodia].mean(),
                      "pct_demanda_12_13h": 100 * (x[a_mediodia] / serie["demanda_MW"][a_mediodia]).mean()})
    t = pd.DataFrame(filas)
    total = t[["energia_GWh", "media_12_13h_MW", "pct_demanda_12_13h"]].sum()
    t.loc[len(t)] = {"categoria": "Total solar", **total.to_dict(), "pico_MW": serie["solar_total_MW"].max()}
    return t


def indicadores(serie: pd.DataFrame) -> dict:
    """Cifras clave del mediodía solar, la rampa de la tarde y la inercia."""
    p = perfil_horario(serie)
    neta = serie["demanda_neta_MW"]
    rampa = neta.diff().where(neta.index.hour != 0)
    h_sol = int(p["solar_total_MW"].idxmax())
    out = {
        "dias": int(len(serie) // 24),
        "horas": int(len(serie)),
        "demanda_GWh": serie["demanda_MW"].sum() / 1e3,
        "demanda_media_MW": serie["demanda_MW"].mean(),
        "demanda_max_MW": serie["demanda_MW"].max(),
        "demanda_max_cuando": str(serie["demanda_MW"].idxmax()),
        "hora_pico_demanda_promedio": int(p["demanda_MW"].idxmax()),
        "hora_pico_solar_promedio": h_sol,
        "demanda_en_pico_solar_MW": p.loc[h_sol, "demanda_MW"],
        "solar_total_en_pico_MW": p.loc[h_sol, "solar_total_MW"],
        "solar_no_despachada_en_pico_MW": p.loc[h_sol, "Solar no despachada_MW"],
        "solar_en_pico_pct_demanda": p.loc[h_sol, "solar_pct_demanda"],
        "demanda_neta_en_pico_solar_MW": p.loc[h_sol, "demanda_neta_MW"],
        "solar_max_instantanea_pct": serie["solar_pct_demanda"].max(),
        "solar_max_instantanea_cuando": str(serie["solar_pct_demanda"].idxmax()),
        "demanda_neta_min_MW": neta.min(),
        "demanda_neta_min_cuando": str(neta.idxmin()),
        "rampa_tarde_promedio_MW_h": p["demanda_neta_MW"].diff().max(),
        "rampa_tarde_promedio_hora": int(p["demanda_neta_MW"].diff().idxmax()),
        "rampa_max_instantanea_MW_h": rampa.max(),
        "rampa_max_instantanea_cuando": str(rampa.idxmax()),
        "precio_bolsa_medio_COP_kWh": serie["precio_bolsa_COP_kWh"].mean(),
    }
    # Quién cubre la subida de la tarde: del pico solar al pico de demanda neta (perfil promedio).
    h_pico = int(p["demanda_neta_MW"].idxmax())
    sube = p.loc[h_pico] - p.loc[h_sol]
    out.update({
        "subida_tarde_desde_hora": h_sol, "subida_tarde_hasta_hora": h_pico,
        "subida_tarde_demanda_neta_MW": sube["demanda_neta_MW"],
        "subida_tarde_hidraulica_MW": sube["Hidráulica_MW"],
        "subida_tarde_termica_MW": sube["Térmica_MW"],
        "subida_tarde_hidraulica_pct": 100 * sube["Hidráulica_MW"] / sube["demanda_neta_MW"],
    })
    if "inercia_GWs" in serie:
        e = serie["inercia_GWs"]
        out.update({
            "inercia_media_GWs": e.mean(),
            "inercia_min_GWs": e.min(),
            "inercia_min_cuando": str(e.idxmin()),
            "inercia_max_GWs": e.max(),
            "H_sistema_min_s": serie["H_sistema_s"].min(),
            "hora_inercia_min_promedio": int(p["inercia_GWs"].idxmin()),
            "no_sincronica_max_pct": serie["no_sincronica_pct"].max(),
        })
    return out


def mezcla_operacion(cfg: dict) -> pd.DataFrame:
    """Energía por tipo: real (Gene) frente al despacho ideal (GeneIdea), con la generación de
    seguridad y fuera de mérito que explican la diferencia (GWh)."""
    x = FuenteXM(cfg["datos"]["cache"])
    ini, fin = cfg["periodo"]["inicio"], cfg["periodo"]["fin"]
    tipo = x.listado_recursos().set_index("codigo")["tipo"]
    cols = {"Gene": "real_GWh", "GeneIdea": "ideal_GWh", "GeneSeguridad": "seguridad_GWh",
            "GeneFueraMerito": "fuera_merito_GWh"}
    t = {}
    for m, nombre in cols.items():
        s = x.serie(m, "Recurso", ini, fin)
        t[nombre] = s.groupby(tipo.reindex(s["codigo"]).fillna("OTRO").to_numpy())["valor"].sum() / 1e6
    t = pd.DataFrame(t).fillna(0.0)
    t.loc["TOTAL"] = t.sum()
    t["real_pct"] = 100 * t["real_GWh"] / t.loc["TOTAL", "real_GWh"]
    t["ideal_pct"] = 100 * t["ideal_GWh"] / t.loc["TOTAL", "ideal_GWh"]
    t.index.name = "tipo"
    return t

"""Página web del estudio (three.js, scroll continuo) con los datos de la última corrida.

`generar_web(cfg, c)` arma un JSON con las series horarias reales del SIN, la validación, los
escenarios, el barrido y el óptimo, y lo incrusta en la plantilla `despacho/web/plantilla.html`.
Escribe en `resultados/web/`:
  - datos.json     los datos, por si se quieren usar en otra herramienta
  - index.html     página autónoma para abrir con doble clic
  - landing.html   la misma página sin <!doctype>/<head>, para publicarla como Artifact
Cada `python run.py` regenera los tres archivos, así la página siempre muestra la última corrida.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .sistema import SOLAR_NO_DESPACHADA

PLANTILLA = Path(__file__).parent / "web" / "plantilla.html"
MARCA = "/*__DATOS__*/null"

ETIQUETAS = {
    "sin_fncer": "Sin solar ni eólica",
    "base": "Base (lo instalado)",
    "solar_+1000": "+1.000 MW solar",
    "solar_+3000": "+3.000 MW solar",
    "eolica_+1000": "+1.000 MW eólica",
    "solar_+2000_eolica_+1000": "+2.000 solar y +1.000 eólica",
    "solar_+2000_distribuida": "+2.000 MW solar distribuida",
    "optimo": "Óptimo de costo",
}


def _r(x, nd=0):
    """Redondea para el JSON; NaN/inf pasan a None."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return x
    if not math.isfinite(v):
        return None
    return round(v, nd) if nd else int(round(v))


def _arr(x, nd=0) -> list:
    return [_r(v, nd) for v in np.asarray(x, dtype=float)]


def _perfil(x, tiempo) -> list:
    """Promedio por hora del día (24 valores, hora XM 1..24)."""
    s = pd.Series(np.asarray(x, dtype=float), index=tiempo)
    return s.groupby(tiempo.hour).mean().to_numpy()


def _escenario(nombre, ideal, fila) -> dict:
    t = ideal.ins.tiempo
    gen = ideal.generacion_por_tipo()
    col = lambda *ks: sum((gen[k] for k in ks if k in gen), pd.Series(0.0, index=t))  # noqa: E731
    solar = col("SOLAR", "SOLAR_NUEVA")
    eolica = col("EOLICA", "EOLICA_NUEVA")
    hidra, term = col("HIDRAULICA"), col("TERMICA")
    otros = gen.drop(columns=[c for c in gen if c in {"SOLAR", "SOLAR_NUEVA", "EOLICA", "EOLICA_NUEVA",
                                                       "HIDRAULICA", "TERMICA"}]).sum(axis=1)
    neta = ideal.ins.demanda - solar.to_numpy() - eolica.to_numpy()
    g = lambda k: _r(fila.get(k), 1)  # noqa: E731
    return {
        "id": nombre,
        "nombre": ETIQUETAS.get(nombre, nombre),
        "solar_MW": _r(fila.get("solar_nueva_MW")),
        "eolica_MW": _r(fila.get("eolica_nueva_MW")),
        "modo": fila.get("modo"),
        "precio": g("precio_bolsa_ponderado_COP_kWh"),
        "precio_pico": g("precio_bolsa_pico_18_21h_COP_kWh"),
        "dprecio": g("delta_precio_pct"),
        "emisiones": g("emisiones_ktCO2e"),
        "demisiones": g("delta_emisiones_pct"),
        "fncer_pct": g("participacion_fncer_pct"),
        "vertimiento_pct": g("vertimiento_fncer_nueva_pct"),
        "reserva_req": g("reserva_req_media_MW"),
        "reserva_costo": _r(fila.get("reserva_costo_milmill_COP"), 2),
        "inercia_media": g("inercia_media_GWs"),
        "inercia_min": g("inercia_min_GWs"),
        "inercia_min_cuando": fila.get("inercia_min_fecha_hora"),
        "snsp_max": g("no_sincronica_max_pct"),
        "perfil": {k: _arr(_perfil(v, t)) for k, v in
                   {"hidraulica": hidra, "termica": term, "otros": otros, "eolica": eolica, "solar": solar}.items()},
        "horas": {
            "neta": _arr(neta),
            "inercia": _arr(ideal.inercia(), 1),
            "precio": _arr(ideal.precio_bolsa, 1),
        },
    }


def datos_web(cfg: dict, c: dict) -> dict:
    ins, s = c["ins"], c["serie_real"]
    t = ins.tiempo
    res_val, metr, _tec, _rec = c["val"]
    tabla = c["tabla"].set_index("escenario")
    ind = c["ind_sin"]

    real = {
        "demanda": _arr(s["demanda_MW"]),
        "hidraulica": _arr(s["Hidráulica_MW"]),
        "termica": _arr(s["Térmica_MW"]),
        "otros": _arr(s["Otros sincrónicos_MW"]),
        "eolica": _arr(s["Eólica_MW"]),
        "solar_desp": _arr(s["Solar despachada_MW"]),
        "solar_nodesp": _arr(s["Solar no despachada_MW"]),
        "solar": _arr(s["solar_total_MW"]),
        "neta": _arr(s["demanda_neta_MW"]),
        "snsp": _arr(s["no_sincronica_pct"], 1),
        "precio": _arr(s["precio_bolsa_COP_kWh"], 1),
    }
    if "inercia_GWs" in s:
        real["inercia"] = _arr(s["inercia_GWs"], 1)

    diario = c["diario"].copy()
    diario.insert(0, "fecha", [str(x) for x in diario.index])
    dias = [{k: (_r(v, 1) if isinstance(v, (float, np.floating)) else v) for k, v in fila.items()}
            for fila in diario.to_dict("records")]

    desglose = [{"categoria": f["categoria"], "energia_GWh": _r(f["energia_GWh"], 1), "pico_MW": _r(f["pico_MW"]),
                 "media_12h_MW": _r(f["media_12_13h_MW"]), "pct_12h": _r(f["pct_demanda_12_13h"], 1)}
                for f in c["desglose"].to_dict("records")]

    mezcla = [{"tipo": tipo, **{k: _r(v, 1) for k, v in f.items()}}
              for tipo, f in c["mezcla"].to_dict("index").items()]

    escenarios = [_escenario(n, ideal, tabla.loc[n].to_dict())
                  for n, (ideal, _) in c["resultados"].items() if n in tabla.index]

    barr = []
    if c.get("barrido") is not None and not c["barrido"].empty:
        for f in c["barrido"].to_dict("records"):
            barr.append({"solar": _r(f["solar_nueva_MW"]), "eolica": _r(f["eolica_nueva_MW"]),
                         "precio": _r(f["precio_bolsa_ponderado_COP_kWh"], 1), "dprecio": _r(f["delta_precio_pct"], 1),
                         "demisiones": _r(f["delta_emisiones_pct"], 1), "inercia_min": _r(f.get("inercia_min_GWs"), 1),
                         "reserva_costo": _r(f.get("reserva_costo_milmill_COP"), 2),
                         "vertimiento_pct": _r(f.get("vertimiento_fncer_nueva_pct"), 1)})

    sens = []
    if c.get("sens") is not None:
        for f in c["sens"].to_dict("records"):
            sens.append({"factor": f["factor_reserva"], "req": _r(f.get("reserva_req_media_MW"), 1),
                         "costo": _r(f.get("reserva_costo_milmill_COP"), 2),
                         "horas_faltante": _r(f.get("reserva_horas_faltante"))})

    o = c["optimo"]
    optimo = {k: _r(v, 3) for k, v in o.items() if isinstance(v, (int, float, np.floating))}

    return {
        "meta": {
            "inicio": str(t[0].date()), "fin": str(t[-1].date()), "dias": int(ins.dias), "horas": int(ins.P),
            "dia_semana_inicio": int(t[0].weekday()),
            "generado": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "fuentes": ["API XM (pydataxm): Gene, GeneIdea, DemaCome, PrecOferIdeal, DispoCome, PrecBolsNaci",
                        "SIMEM: inflexibilidades (FE1E73), holgura AGC (897043), unidades por planta (670221)"],
            "solar_no_despachada": SOLAR_NO_DESPACHADA,
            "costos": cfg.get("costos", {}),
            "reserva": {k: cfg["reserva"].get(k) for k in ("factor_solar", "factor_eolica")},
        },
        "real": real,
        "ind": {k: (_r(v, 1) if isinstance(v, (float, np.floating)) else v) for k, v in ind.items()},
        "dias": dias,
        "desglose": desglose,
        "mezcla": mezcla,
        "validacion": {
            "metricas": {k: (_r(v, 4) if isinstance(v, (float, np.floating)) else v) for k, v in metr.items()},
            "precio_modelo": _arr(res_val.precio, 1),
            "precio_xm": _arr(ins.precio_marginal_xm, 1),
        },
        "escenarios": escenarios,
        "barrido": barr,
        "optimo": optimo,
        "sensibilidad": sens,
    }


def generar_web(cfg: dict, c: dict) -> Path:
    carpeta = Path(cfg["salidas"]["carpeta"]) / "web"
    carpeta.mkdir(parents=True, exist_ok=True)
    texto = json.dumps(datos_web(cfg, c), ensure_ascii=False, separators=(",", ":"))
    (carpeta / "datos.json").write_text(texto, encoding="utf-8")
    return construir_pagina(carpeta)


def construir_pagina(carpeta: Path) -> Path:
    """Incrusta `carpeta/datos.json` en la plantilla. Sirve para rehacer la página sin correr el modelo."""
    texto = (carpeta / "datos.json").read_text(encoding="utf-8")
    plantilla = PLANTILLA.read_text(encoding="utf-8")
    if MARCA not in plantilla:
        raise ValueError(f"La plantilla no tiene el marcador {MARCA!r}")
    pagina = plantilla.replace(MARCA, texto.replace("</", "<\\/"))
    (carpeta / "landing.html").write_text(pagina, encoding="utf-8")
    cabecera = ('<!doctype html>\n<html lang="es">\n<meta charset="utf-8">\n'
                '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n')
    (carpeta / "index.html").write_text(cabecera + pagina + "\n</html>\n", encoding="utf-8")
    return carpeta / "index.html"


if __name__ == "__main__":  # python -m despacho.web [carpeta]  → rehace la página con el datos.json existente
    import sys
    print(construir_pagina(Path(sys.argv[1] if len(sys.argv) > 1 else "resultados/web")))

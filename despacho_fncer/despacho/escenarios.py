"""Escenarios de entrada de FNCER, barrido de capacidades y optimización de la entrada.

Cada escenario se resuelve dos veces, igual que en el mercado colombiano:
  1. Despacho ideal (sin reserva): fija el precio de bolsa, el mix y las emisiones.
  2. Despacho con reserva AGC co-optimizada: mide cuánta reserva exige el escenario y cuánto
     cuesta (diferencia de costo frente al despacho ideal y precio sombra de la reserva).
"""
from __future__ import annotations

import logging
from dataclasses import replace

import numpy as np
import pandas as pd

from .modelo import Escenario, Parametros, Resultado, despachar
from .preparacion import FNCER, TIPO_FNCER, Insumos

log = logging.getLogger(__name__)

MIL_MILLONES = 1e6  # el objetivo está en miles de COP


def indicadores(ideal: Resultado, con_reserva: Resultado | None = None) -> dict:
    ins, esc = ideal.ins, ideal.escenario
    dem = ins.demanda
    pb = ideal.precio_bolsa
    gen = ideal.generacion_por_tipo().sum() / 1e3  # GWh
    fncer_gwh = sum(gen.get(t, 0.0) for t in ("SOLAR", "EOLICA", "SOLAR_NUEVA", "EOLICA_NUEVA"))
    fila = {
        "escenario": esc.nombre,
        "modo": esc.modo,
        "solar_nueva_MW": ideal.capacidad["solar"],
        "eolica_nueva_MW": ideal.capacidad["eolica"],
        "retira_existentes": esc.retirar_existentes,
        "demanda_GWh": dem.sum() / 1e3,
        "precio_bolsa_medio_COP_kWh": pb.mean(),
        "precio_bolsa_ponderado_COP_kWh": (pb * dem).sum() / dem.sum(),
        "precio_bolsa_pico_18_21h_COP_kWh": pb[np.isin(ins.tiempo.hour, [18, 19, 20])].mean(),
        "valor_compras_bolsa_milmill_COP": (pb * dem).sum() / MIL_MILLONES,
        "costo_despacho_ofertas_milmill_COP": ideal.costo_despacho().sum() / MIL_MILLONES,
        "participacion_fncer_pct": 100 * fncer_gwh / (dem.sum() / 1e3),
        "emisiones_ktCO2e": ideal.emisiones().sum() / 1e3,
        "deficit_GWh": ideal.deficit.sum() / 1e3,
    }
    for t in ("HIDRAULICA", "TERMICA", "SOLAR", "EOLICA", "SOLAR_NUEVA", "EOLICA_NUEVA"):
        fila[f"gen_{t}_GWh"] = gen.get(t, 0.0)
    disponible = sum(ideal.disponible_nueva[k].sum() for k in FNCER)
    inyectada = sum(ideal.y[k].sum() for k in FNCER)
    fila["vertimiento_fncer_nueva_GWh"] = (disponible - inyectada) / 1e3 + ideal.vertimiento.sum() / 1e3
    fila["vertimiento_fncer_nueva_pct"] = 100 * (disponible - inyectada) / disponible if disponible > 0 else 0.0
    for k in FNCER:
        yk = ideal.y[k]
        fila[f"precio_capturado_{k}_nueva_COP_kWh"] = (pb * yk).sum() / yk.sum() if yk.sum() > 1 else np.nan
    if "H_s" in ins.recursos:
        E, snsp = ideal.inercia(), ideal.snsp()
        fila.update({
            "inercia_media_GWs": E.mean(),
            "inercia_min_GWs": E.min(),
            "inercia_min_fecha_hora": str(ins.tiempo[E.argmin()]),
            "H_sistema_min_s": (E / (dem / 1e3)).min(),
            "no_sincronica_12h_media_pct": 100 * snsp[ins.tiempo.hour == 12].mean(),
            "no_sincronica_max_pct": 100 * snsp.max(),
        })
    if con_reserva is not None and con_reserva.reserva:
        rv = con_reserva.reserva
        fila.update({
            "reserva_req_media_MW": rv["requerimiento"].mean(),
            "reserva_req_max_MW": rv["requerimiento"].max(),
            "reserva_costo_milmill_COP": (con_reserva.objetivo - ideal.objetivo) / MIL_MILLONES,
            "reserva_precio_medio_subir_COP_kWh": rv["precio_subir"].mean(),
            "reserva_horas_precio_positivo": int((rv["precio_subir"] > 1).sum()),
            "reserva_horas_faltante": int(((rv["faltante_subir"] > 0.1) | (rv["faltante_bajar"] > 0.1)).sum()),
            "reserva_faltante_MWh": float(rv["faltante_subir"].sum() + rv["faltante_bajar"].sum()),
        })
    return fila


def _correr(ins: Insumos, par: Parametros, esc: Escenario) -> tuple[Resultado, Resultado]:
    ideal = despachar(ins, replace(esc, reserva=False), par)
    con_reserva = despachar(ins, replace(esc, reserva=True), par)
    return ideal, con_reserva


def agregar_deltas(tabla: pd.DataFrame, referencia: str = "base") -> pd.DataFrame:
    if referencia not in set(tabla["escenario"]):
        return tabla
    ref = tabla.set_index("escenario").loc[referencia]
    for col, nombre in (("precio_bolsa_ponderado_COP_kWh", "delta_precio_pct"),
                        ("emisiones_ktCO2e", "delta_emisiones_pct"),
                        ("costo_despacho_ofertas_milmill_COP", "delta_costo_despacho_pct"),
                        ("valor_compras_bolsa_milmill_COP", "delta_valor_bolsa_pct")):
        tabla[nombre] = 100 * (tabla[col] / ref[col] - 1)
    if "reserva_costo_milmill_COP" in tabla:
        tabla["delta_costo_reserva_milmill_COP"] = tabla["reserva_costo_milmill_COP"] - ref["reserva_costo_milmill_COP"]
    return tabla


def correr_escenarios(ins: Insumos, par: Parametros, escenarios: list[Escenario]):
    filas, resultados = [], {}
    for esc in escenarios:
        log.info("Escenario %s", esc.nombre)
        ideal, con_res = _correr(ins, par, esc)
        resultados[esc.nombre] = (ideal, con_res)
        filas.append(indicadores(ideal, con_res))
    return agregar_deltas(pd.DataFrame(filas)), resultados


def barrido(ins: Insumos, par: Parametros, solar_mw, eolica_mw, modo="despachada") -> pd.DataFrame:
    filas = []
    for s in solar_mw:
        for e in eolica_mw:
            esc = Escenario(f"S{s:g}_E{e:g}", solar_mw=s, eolica_mw=e, modo=modo)
            filas.append(indicadores(*_correr(ins, par, esc)))
    t = pd.DataFrame(filas)
    t = agregar_deltas(t, referencia="S0_E0")
    return t


def optimizar_entrada(ins: Insumos, par: Parametros, cfg: dict, modo="despachada"):
    """Capacidad solar/eólica que minimiza costo de despacho + costo anualizado de inversión.

    La capacidad entra al LP como variable continua compartida por todas las horas del periodo,
    con su costo anual prorrateado a los días simulados.
    """
    o = cfg["optimizacion"]
    esc = Escenario("optimo", modo=modo, optimizar_capacidad=True, reserva=o["con_reserva"],
                    solar_max_mw=o["solar_max_mw"], eolica_max_mw=o["eolica_max_mw"])
    res = despachar(ins, esc, par)
    fijo = replace(esc, optimizar_capacidad=False, solar_mw=res.capacidad["solar"],
                   eolica_mw=res.capacidad["eolica"])
    ideal, con_res = _correr(ins, par, fijo)
    info = {"escenario": "optimo", "con_reserva": o["con_reserva"]}
    for k in FNCER:
        cf_medio = ins.cf[k].mean()
        lcoe = par.costo_anual_mw[k] / (8760 * cf_medio * 1000) if cf_medio > 0 else np.inf
        info.update({
            f"{k}_optimo_MW": res.capacidad[k],
            f"{k}_limite_MW": esc.capacidad_max(k),
            f"{k}_factor_planta_medio": cf_medio,
            f"{k}_costo_anual_COP_MW": par.costo_anual_mw[k],
            f"{k}_lcoe_implicito_COP_kWh": lcoe,
        })
    return info, (ideal, con_res)


def sensibilidad_reserva(ins: Insumos, par: Parametros, esc: Escenario, factores) -> pd.DataFrame:
    """Repite un escenario con distintos MW de AGC exigidos por MW de FNCER nueva generado."""
    filas = []
    for f in factores:
        p = replace(par, factor_reserva={k: f for k in FNCER})
        fila = indicadores(*_correr(ins, p, esc))
        filas.append({"escenario": esc.nombre, "factor_reserva": f,
                      **{k: v for k, v in fila.items() if k.startswith("reserva_")}})
    return pd.DataFrame(filas)


def capacidad_existente(ins: Insumos) -> dict:
    out = {}
    for k in FNCER:
        out[f"{k}_capacidad_activa_media_MW"] = ins.capacidad_fncer_existente.get(k, 0.0)
        out[f"{k}_generacion_GWh"] = ins.fncer_existente[k].sum() / 1e3
        out[f"{k}_factor_planta_medio"] = float(ins.cf[k].mean())
    return out

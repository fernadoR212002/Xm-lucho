"""Simulador de despacho del SIN con y sin FNCER.

Uso:
  .venv/bin/python run.py                 # todo: validación, escenarios, barrido, optimización y reporte
  .venv/bin/python run.py validar         # solo la validación del modelo base contra XM
  .venv/bin/python run.py todo --sin-barrido --config otra_config.yaml
"""
from __future__ import annotations

import argparse
import logging
import time

import pandas as pd

from despacho import sistema
from despacho.config import cargar_config, escenarios, parametros
from despacho.datos import FuenteXM
from despacho.escenarios import (agregar_deltas, barrido, capacidad_existente, correr_escenarios,
                                 indicadores, optimizar_entrada, sensibilidad_reserva)
from despacho.preparacion import construir_insumos
from despacho.reporte import generar_reporte
from despacho.validacion import correr_validacion, tabla_recursos
from despacho.web import generar_web


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("accion", nargs="?", default="todo", choices=["todo", "validar"])
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--sin-barrido", action="store_true", help="omite el barrido de capacidades")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    log = logging.getLogger("run")
    t0 = time.time()

    cfg = cargar_config(a.config)
    ins = construir_insumos(cfg)
    par = parametros(cfg, ins)

    res_val, metr, tec = correr_validacion(ins, par, cfg["validacion"])
    log.info("Validación: %s | error precio %.1f %% | error energía diaria %.1f %%",
             metr["veredicto"], 100 * metr["mape_precio"], 100 * metr["wape_energia_diaria"])
    print("\n" + tec.round(2).to_string() + "\n")
    if a.accion == "validar":
        return
    if metr["veredicto"] != "ACEPTADO":
        log.warning("El modelo base no pasó la validación; revisa supuestos antes de leer los escenarios.")

    tabla, resultados = correr_escenarios(ins, par, escenarios(cfg))

    optimo, (opt_ideal, opt_res) = optimizar_entrada(ins, par, cfg, modo=cfg["fncer"]["modo"])
    resultados["optimo"] = (opt_ideal, opt_res)
    tabla = agregar_deltas(pd.concat([tabla, pd.DataFrame([indicadores(opt_ideal, opt_res)])], ignore_index=True))

    barr = None
    if not a.sin_barrido:
        b = cfg["barrido"]
        barr = barrido(ins, par, b["solar_mw"], b["eolica_mw"], modo=cfg["fncer"]["modo"])

    sens = None
    factores = cfg["reserva"].get("sensibilidad")
    nombre_sens = cfg["reserva"].get("escenario_sensibilidad")
    if factores and nombre_sens in resultados:
        sens = sensibilidad_reserva(ins, par, resultados[nombre_sens][0].escenario, factores)

    serie = sistema.serie_real(ins)
    contenido = {
        "ins": ins, "listado": FuenteXM(cfg["datos"]["cache"]).listado_recursos().set_index("codigo"),
        "serie_real": serie, "perfil": sistema.perfil_horario(serie), "diario": sistema.resumen_diario(serie),
        "desglose": sistema.desglose_solar(ins, serie), "mezcla": sistema.mezcla_operacion(cfg),
        "ind_sin": sistema.indicadores(serie) | capacidad_existente(ins),
        "val": (res_val, metr, tec, tabla_recursos(res_val)),
        "tabla": tabla, "resultados": resultados, "barrido": barr, "optimo": optimo, "sens": sens,
    }
    salidas = generar_reporte(cfg, contenido)
    pagina = generar_web(cfg, contenido)

    cols = ["escenario", "solar_nueva_MW", "eolica_nueva_MW", "precio_bolsa_ponderado_COP_kWh",
            "delta_precio_pct", "emisiones_ktCO2e", "delta_emisiones_pct", "reserva_req_media_MW",
            "reserva_costo_milmill_COP"]
    print("\n" + tabla[cols].round(1).to_string(index=False) + "\n")
    log.info("Óptimo: solar %.0f MW, eólica %.0f MW", optimo["solar_optimo_MW"], optimo["eolica_optimo_MW"])
    log.info("Reporte: %s | Excel: %s | Web: %s", salidas["reporte"], salidas["excel"], pagina)
    log.info("Terminado en %.0f s", time.time() - t0)


if __name__ == "__main__":
    main()

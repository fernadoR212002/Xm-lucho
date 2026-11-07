"""Lectura de config.yaml y traducción a parámetros del modelo."""
from __future__ import annotations

from pathlib import Path

import yaml

from .modelo import Escenario, Parametros
from .preparacion import FNCER, Insumos


def cargar_config(ruta: str | Path) -> dict:
    ruta = Path(ruta)
    with open(ruta, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    base = ruta.resolve().parent
    for seccion, clave in (("datos", "cache"), ("salidas", "carpeta")):
        p = Path(cfg[seccion][clave])
        cfg[seccion][clave] = str(p if p.is_absolute() else base / p)
    return cfg


def anualidad(tasa: float, n: int) -> float:
    """Factor de recuperación de capital."""
    return tasa * (1 + tasa) ** n / ((1 + tasa) ** n - 1)


def costo_anual_mw(cfg: dict) -> dict[str, float]:
    """COP por MW-año (inversión anualizada + O&M) para cada tecnología FNCER."""
    c = cfg["fncer"]["costos"]
    crf = anualidad(c["wacc"], c["vida_anios"])
    return {k: (c[k]["capex_usd_mw"] * crf + c[k]["om_usd_mw_anio"]) * c["trm"] for k in FNCER}


def parametros(cfg: dict, ins: Insumos) -> Parametros:
    precio = cfg["fncer"]["precio_oferta"]
    precio = ins.precio_fncer_ref if precio == "auto" else float(precio)
    return Parametros(
        costo_racionamiento=cfg["modelo"]["costo_racionamiento"],
        usar_inflexibilidades=cfg["modelo"]["usar_inflexibilidades"],
        costo_deficit_reserva=cfg["reserva"]["costo_deficit"],
        factor_reserva={"solar": cfg["reserva"]["factor_solar"], "eolica": cfg["reserva"]["factor_eolica"]},
        precio_oferta_fncer={k: precio for k in FNCER},
        costo_anual_mw=costo_anual_mw(cfg),
    )


def escenarios(cfg: dict) -> list[Escenario]:
    modo = cfg["fncer"]["modo"]
    return [Escenario(**{"modo": modo, **e}) for e in cfg["escenarios"]]

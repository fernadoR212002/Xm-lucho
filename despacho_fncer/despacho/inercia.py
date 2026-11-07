"""Estimación de la inercia sincrónica del SIN.

XM y SIMEM no publican la inercia del sistema, así que se estima la energía cinética guardada en
las máquinas sincrónicas que están en línea cada hora:

    E(t) = Σ_i H_i · S_i(t)                                   [MW·s]

  H_i     constante de inercia típica de la tecnología (s, en base de la máquina).
  S_i(t)  MVA en línea de la planta i = unidades en línea × tamaño de unidad / factor de potencia.
          unidades en línea = ⌈g_i(t) / tamaño de unidad⌉; tamaño = capacidad efectiva / número de
          unidades de la planta (SIMEM 670221).

Se supone el mínimo de unidades necesario para la generación de cada hora, así que el resultado es
una cota inferior: en la operación real puede haber más unidades en línea a carga parcial.
Solar y eólica se conectan con inversores y no aportan inercia sincrónica (H = 0).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def energia_cinetica(G: np.ndarray, H, cap_mw, n_unidades, factor_potencia: float) -> np.ndarray:
    """Energía cinética sincrónica por hora en GW·s.

    G: (R,P) MW generados; H, cap_mw, n_unidades: (R,) por recurso.
    """
    H = np.nan_to_num(np.asarray(H, dtype=float))
    n = np.maximum(np.nan_to_num(np.asarray(n_unidades, dtype=float), nan=1.0), 1.0)
    cap = np.nan_to_num(np.asarray(cap_mw, dtype=float))
    cap = np.maximum(cap, np.nanmax(np.where(G > 0, G, 0.0), axis=1, initial=0.0))
    u = np.divide(cap, n, out=np.zeros_like(cap), where=n > 0)
    en_linea = np.ceil(np.divide(G, u[:, None], out=np.zeros_like(G), where=u[:, None] > 0) - 1e-6)
    en_linea = np.clip(en_linea, 0.0, n[:, None])
    S = en_linea * u[:, None] / factor_potencia
    return (H[:, None] * S).sum(axis=0) / 1000.0


def tabla_maquinas(codigos, tipos: pd.Series, cap_mw: pd.Series, unidades: pd.DataFrame,
                   H_por_tipo: dict) -> pd.DataFrame:
    """Parámetros por recurso: H (s), capacidad (MW) y número de unidades."""
    if unidades.empty:
        n = pd.Series(dtype=float)
    else:
        n = unidades.groupby("CodigoPlanta")["CodigoUnidadGeneracion"].nunique()
    t = pd.DataFrame(index=pd.Index(list(codigos), name="codigo"))
    t["tipo"] = tipos.reindex(t.index).fillna("OTRO").to_numpy()
    t["H_s"] = t["tipo"].map(H_por_tipo).fillna(0.0)
    t["cap_mw"] = cap_mw.reindex(t.index).to_numpy()
    t["n_unidades"] = n.reindex(t.index).fillna(1).astype(float).to_numpy()
    return t

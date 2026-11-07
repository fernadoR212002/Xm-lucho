"""Despacho económico uninodal horario del SIN como programa lineal (HiGHS vía scipy).

Formulación (p = hora, r = recurso con oferta, k ∈ {solar, eólica} nuevas):

  min  Σ c[r,p]·g[r,p] + Σ c_k·y[k,p] + VoLL·d[p] + c_v·v[p] + Σ CF_k·C_k
       + ε·Σ(ru + rd) + c_rf·Σ(su + sd)

  Balance       Σ_r g[r,p] + Σ_k y[k,p] + d[p] − v[p] = D_res[p]          (dual = costo marginal)
  Límites       inflex[r,p] ≤ g[r,p] ≤ disp[r,p]
  FNCER nueva   y[k,p] ≤ cf[k,p]·C_k   (despachada: puede verterse)
                y[k,p] = cf[k,p]·C_k   (distribuida: inyección obligatoria)
  Capacidad     C_k fija (escenario) o 0 ≤ C_k ≤ C_max (optimización de entrada)

  Reserva AGC (solo si el escenario la activa; a = plantas que prestan AGC):
                g[a,p] + ru[a,p] ≤ disp[a,p]
                g[a,p] − rd[a,p] ≥ inflex[a,p]
                Σ_a ru[a,p] + su[p] ≥ R0[p] + Σ_k β_k·cf[k,p]·C_k          (dual = precio reserva)
                Σ_a rd[a,p] + sd[p] ≥ R0[p] + Σ_k β_k·cf[k,p]·C_k

Unidades: variables en MW (= MWh por hora), costos en COP/kWh, así el objetivo queda en
miles de COP y el dual del balance sale directamente en COP/kWh.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.optimize import linprog

from .inercia import energia_cinetica
from .preparacion import FNCER, TIPO_FNCER, Insumos

log = logging.getLogger(__name__)


@dataclass
class Parametros:
    costo_racionamiento: float = 5000.0      # COP/kWh de demanda no atendida
    costo_vertimiento: float = 0.01          # COP/kWh, desempate para excedentes forzados
    costo_reserva: float = 0.01              # COP/kWh por MW reservado, desempate
    costo_deficit_reserva: float = 3000.0    # COP/kWh por MW de reserva no cubierta
    usar_inflexibilidades: bool = True
    factor_reserva: dict = field(default_factory=lambda: {"solar": 0.05, "eolica": 0.08})
    precio_oferta_fncer: dict = field(default_factory=lambda: {"solar": 0.0, "eolica": 0.0})
    costo_anual_mw: dict = field(default_factory=lambda: {"solar": 0.0, "eolica": 0.0})  # COP/MW-año


@dataclass
class Escenario:
    nombre: str
    solar_mw: float = 0.0
    eolica_mw: float = 0.0
    modo: str = "despachada"               # "despachada" | "distribuida"
    retirar_existentes: bool = False       # contrafactual sin la solar/eólica que ya opera
    reserva: bool = False                  # co-optimizar la reserva AGC
    optimizar_capacidad: bool = False
    solar_max_mw: float = 0.0
    eolica_max_mw: float = 0.0

    def capacidad(self, k: str) -> float:
        return {"solar": self.solar_mw, "eolica": self.eolica_mw}[k]

    def capacidad_max(self, k: str) -> float:
        return {"solar": self.solar_max_mw, "eolica": self.eolica_max_mw}[k]


class _ConstructorLP:
    """Acumula variables y restricciones como matrices dispersas (formato COO)."""

    def __init__(self):
        self.n = 0
        self._c, self._lb, self._ub = [], [], []
        self._filas = {"eq": [[], [], [], []], "ub": [[], [], [], []]}  # fila, col, val, rhs
        self.m = {"eq": 0, "ub": 0}

    def variables(self, forma, lb=0.0, ub=np.inf, costo=0.0) -> np.ndarray:
        k = int(np.prod(forma))
        idx = np.arange(self.n, self.n + k).reshape(forma)
        self.n += k
        for lista, v in ((self._c, costo), (self._lb, lb), (self._ub, ub)):
            lista.append(np.broadcast_to(np.asarray(v, dtype=float), forma).ravel())
        return idx

    def restricciones(self, sentido: str, rhs, terminos) -> np.ndarray:
        """Añade len(rhs) filas. `terminos` = [(fila_local, columna, coeficiente), ...] en arrays."""
        rhs = np.atleast_1d(np.asarray(rhs, dtype=float))
        base = self.m[sentido]
        f, c, v, b = self._filas[sentido]
        for fila, col, coef in terminos:
            fila, col = np.asarray(fila).ravel(), np.asarray(col).ravel()
            f.append(base + fila)
            c.append(col)
            v.append(np.broadcast_to(np.asarray(coef, dtype=float), fila.shape).ravel())
        b.append(rhs)
        self.m[sentido] += len(rhs)
        return np.arange(base, base + len(rhs))

    def resolver(self):
        def matriz(s):
            f, c, v, b = self._filas[s]
            if not b:
                return None, None
            A = sp.coo_matrix((np.concatenate(v), (np.concatenate(f), np.concatenate(c))),
                              shape=(self.m[s], self.n)).tocsr()
            return A, np.concatenate(b)

        A_eq, b_eq = matriz("eq")
        A_ub, b_ub = matriz("ub")
        bounds = np.column_stack([np.concatenate(self._lb), np.concatenate(self._ub)])
        res = linprog(np.concatenate(self._c), A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq,
                      bounds=bounds, method="highs")
        if res.status != 0:
            raise RuntimeError(f"El LP no encontró solución óptima: {res.message}")
        return res


@dataclass
class Resultado:
    escenario: Escenario
    ins: Insumos
    par: Parametros
    g: np.ndarray                  # (R,P) MW recursos con oferta
    y: dict[str, np.ndarray]       # (P,) MW FNCER nueva inyectada
    capacidad: dict[str, float]    # MW FNCER nueva instalada
    disponible_nueva: dict[str, np.ndarray]  # (P,) MW cf·C
    deficit: np.ndarray
    vertimiento: np.ndarray
    cmg: np.ndarray                # (P,) dual del balance, COP/kWh
    precio: np.ndarray             # (P,) regla XM: mayor oferta de recurso flexible despachado
    marginal: np.ndarray           # (P,) código del recurso que fija el precio
    residual: np.ndarray           # (P,) demanda residual del escenario
    fijo: pd.DataFrame             # (P × tipo) inyección fija del escenario
    objetivo: float                # miles de COP
    reserva: dict | None = None

    @property
    def precio_bolsa(self) -> np.ndarray:
        return self.precio + self.ins.delta_i

    def generacion_por_tipo(self) -> pd.DataFrame:
        rec = self.ins.recursos
        g = pd.DataFrame(self.g.T, index=self.ins.tiempo, columns=rec["tipo"].to_numpy())
        g = g.T.groupby(level=0).sum().T
        tot = g.add(self.fijo, fill_value=0.0)
        for k in FNCER:
            if self.capacidad[k] > 0:
                tot[f"{TIPO_FNCER[k]}_NUEVA"] = self.y[k]
        return tot

    def inercia(self) -> np.ndarray:
        """Energía cinética sincrónica estimada por hora (GW·s); ver despacho/inercia.py."""
        rec = self.ins.recursos
        return energia_cinetica(self.g, rec["H_s"], rec["cap_mw"], rec["n_unidades"],
                                self.ins.factor_potencia) + self.ins.inercia_fija

    def snsp(self) -> np.ndarray:
        """Participación no sincrónica: (solar + eólica) / demanda, por hora."""
        gen = self.generacion_por_tipo()
        no_sinc = sum(gen[c] for c in gen if c.startswith(("SOLAR", "EOLICA")))
        return np.asarray(no_sinc) / self.ins.demanda

    def emisiones(self) -> np.ndarray:
        """tCO2e por hora."""
        f = self.ins.recursos["factor_co2"].to_numpy()
        return (self.g * f[:, None]).sum(axis=0) + self.ins.fijo_co2

    def costo_despacho(self) -> np.ndarray:
        """Costo según precios de oferta, miles de COP por hora."""
        c = (self.ins.costo * self.g).sum(axis=0)
        for k in FNCER:
            c = c + self.par.precio_oferta_fncer[k] * self.y[k]
        return c

    def serie_horaria(self) -> pd.DataFrame:
        df = pd.DataFrame(index=self.ins.tiempo)
        df.index.name = "fecha_hora"
        df["demanda_MW"] = self.ins.demanda
        df["residual_MW"] = self.residual
        df["precio_marginal_COP_kWh"] = self.precio
        df["precio_bolsa_COP_kWh"] = self.precio_bolsa
        df["cmg_dual_COP_kWh"] = self.cmg
        df["recurso_marginal"] = self.marginal
        gen = self.generacion_por_tipo().add_prefix("gen_").add_suffix("_MW")
        df = df.join(gen)
        for k in FNCER:
            if self.capacidad[k] > 0:
                df[f"vertimiento_{k}_MW"] = self.disponible_nueva[k] - self.y[k]
        df["deficit_MW"] = self.deficit
        df["emisiones_tCO2e"] = self.emisiones()
        if "H_s" in self.ins.recursos:
            df["inercia_GWs"] = self.inercia()
            df["no_sincronica_pct"] = 100 * self.snsp()
        if self.reserva:
            for c in ("requerimiento", "asignada_subir", "asignada_bajar", "faltante_subir",
                      "precio_subir", "precio_bajar"):
                unidad = "COP_kWh" if c.startswith("precio") else "MW"
                df[f"reserva_{c}_{unidad}"] = self.reserva[c]
        return df

    def despacho_por_recurso(self) -> pd.DataFrame:
        """Formato largo (recurso, hora, MW) para exportar a PowerFactory u otras herramientas."""
        rec = self.ins.recursos
        df = pd.DataFrame(self.g, index=rec.index, columns=self.ins.tiempo)
        df = df.stack().rename("MW").reset_index().rename(columns={"level_1": "fecha_hora"})
        df = df[df["MW"] > 1e-6]
        df = df.merge(rec[["nombre", "tipo"]], left_on="codigo", right_index=True)
        extra = [pd.DataFrame({"codigo": f"NUEVA_{k.upper()}", "fecha_hora": self.ins.tiempo,
                               "MW": self.y[k], "nombre": f"{k} nueva", "tipo": TIPO_FNCER[k]})
                 for k in FNCER if self.capacidad[k] > 0]
        return pd.concat([df, *extra], ignore_index=True)[["fecha_hora", "codigo", "nombre", "tipo", "MW"]]


def despachar(ins: Insumos, esc: Escenario, par: Parametros) -> Resultado:
    R, P = ins.R, ins.P
    rec = ins.recursos
    lp = _ConstructorLP()

    # --- Ajustes del escenario --------------------------------------------------------------
    disp = ins.disp.copy()
    fijo = ins.fijo.copy()
    req_base = ins.req_agc.copy()
    if esc.retirar_existentes:
        for k in FNCER:
            t = TIPO_FNCER[k]
            disp[(rec["tipo"] == t).to_numpy()] = 0.0
            if t in fijo:
                fijo[t] = 0.0
            req_base = req_base - par.factor_reserva[k] * ins.fncer_existente[k]
        req_base = np.maximum(req_base, 0.0)
    piso = np.minimum(ins.inflex, disp) if par.usar_inflexibilidades else np.zeros_like(disp)
    residual = ins.demanda - fijo.sum(axis=1).to_numpy()

    # --- Variables ---------------------------------------------------------------------------
    g = lp.variables((R, P), lb=piso, ub=disp, costo=ins.costo)
    C, y = {}, {}
    for k in FNCER:
        if esc.optimizar_capacidad:
            lo, hi = 0.0, esc.capacidad_max(k)
            costo_c = par.costo_anual_mw[k] * ins.dias / 365.0 / 1000.0  # miles de COP por MW
        else:
            lo = hi = esc.capacidad(k)
            costo_c = 0.0
        C[k] = lp.variables((1,), lb=lo, ub=hi, costo=costo_c)
        c_y = par.precio_oferta_fncer[k] if esc.modo == "despachada" else 0.0
        y[k] = lp.variables((P,), costo=c_y)
    d = lp.variables((P,), costo=par.costo_racionamiento)
    v = lp.variables((P,), costo=par.costo_vertimiento)

    # --- Balance de energía -----------------------------------------------------------------
    horas = np.arange(P)
    terminos = [(np.tile(horas, R), g.ravel(), 1.0), (horas, d, 1.0), (horas, v, -1.0)]
    terminos += [(horas, y[k], 1.0) for k in FNCER]
    fila_balance = lp.restricciones("eq", residual, terminos)

    # --- FNCER nueva: y ≤ cf·C (despachada) o y = cf·C (distribuida) -------------------------
    sentido = "ub" if esc.modo == "despachada" else "eq"
    for k in FNCER:
        lp.restricciones(sentido, np.zeros(P), [(horas, y[k], 1.0),
                                                (horas, np.repeat(C[k], P), -ins.cf[k])])

    # --- Reserva AGC ---------------------------------------------------------------------------
    filas_res = None
    if esc.reserva:
        a = np.flatnonzero(rec["agc"].to_numpy())
        A = len(a)
        if A == 0:
            raise ValueError("No hay plantas AGC identificadas en el periodo (SIMEM 897043).")
        ru = lp.variables((A, P), costo=par.costo_reserva)
        rd = lp.variables((A, P), costo=par.costo_reserva)
        su = lp.variables((P,), costo=par.costo_deficit_reserva)
        sd = lp.variables((P,), costo=par.costo_deficit_reserva)
        n = np.arange(A * P)
        lp.restricciones("ub", disp[a].ravel(), [(n, g[a].ravel(), 1.0), (n, ru.ravel(), 1.0)])
        lp.restricciones("ub", -piso[a].ravel(), [(n, g[a].ravel(), -1.0), (n, rd.ravel(), 1.0)])
        filas_res = {}
        for nombre, r, s in (("subir", ru, su), ("bajar", rd, sd)):
            t = [(np.tile(horas, A), r.ravel(), -1.0), (horas, s, -1.0)]
            t += [(horas, np.repeat(C[k], P), par.factor_reserva[k] * ins.cf[k]) for k in FNCER]
            filas_res[nombre] = lp.restricciones("ub", -req_base, t)

    # --- Resolver -------------------------------------------------------------------------------
    res = lp.resolver()
    x = res.x
    G = x[g]
    cap = {k: float(x[C[k]][0]) for k in FNCER}
    Y = {k: x[y[k]] for k in FNCER}
    cmg = res.eqlin.marginals[fila_balance]

    # Precio con la regla de XM: mayor precio de oferta entre los recursos flexibles despachados
    # (los que generan por encima de su inflexibilidad).
    flexible = G > piso + 0.1
    precios = np.where(flexible, ins.costo, -np.inf)
    idx = precios.argmax(axis=0)
    precio = precios[idx, horas]
    marginal = rec.index.to_numpy()[idx].astype(object)
    for k in FNCER:
        if esc.modo == "despachada" and cap[k] > 0:
            usa = (Y[k] > 0.1) & (par.precio_oferta_fncer[k] > precio)
            precio = np.where(usa, par.precio_oferta_fncer[k], precio)
            marginal = np.where(usa, f"NUEVA_{k.upper()}", marginal)
    sin_flex = ~np.isfinite(precio)
    precio = np.where(sin_flex, cmg, precio)
    marginal = np.where(sin_flex, "", marginal)
    precio = np.where(x[d] > 0.1, par.costo_racionamiento, precio)

    reserva = None
    if filas_res is not None:
        req = req_base + sum(par.factor_reserva[k] * ins.cf[k] * cap[k] for k in FNCER)
        reserva = {
            "requerimiento": req,
            "asignada_subir": x[ru].sum(axis=0), "asignada_bajar": x[rd].sum(axis=0),
            "faltante_subir": x[su], "faltante_bajar": x[sd],
            "precio_subir": -res.ineqlin.marginals[filas_res["subir"]],
            "precio_bajar": -res.ineqlin.marginals[filas_res["bajar"]],
            "plantas": rec.index[a].tolist(),
        }

    return Resultado(escenario=esc, ins=ins, par=par, g=G, y=Y, capacidad=cap,
                     disponible_nueva={k: ins.cf[k] * cap[k] for k in FNCER},
                     deficit=x[d], vertimiento=x[v], cmg=cmg, precio=precio, marginal=marginal,
                     residual=residual, fijo=fijo, objetivo=float(res.fun), reserva=reserva)

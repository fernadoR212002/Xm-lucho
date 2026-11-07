"""Convierte las series de XM/SIMEM en los insumos numéricos del modelo de despacho.

Convenciones:
  * R recursos con oferta de precio (despachados centralmente) y P horas del periodo.
  * Energía horaria en MWh == potencia media en MW. Precios en COP/kWh.
  * Lo que no oferta precio (plantas menores, autogeneradores, cogeneradores, solar distribuida)
    entra como inyección fija igual a su generación ideal real: así lo trata XM en el despacho ideal.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .datos import FuenteSIMEM, FuenteXM
from .inercia import energia_cinetica, tabla_maquinas

log = logging.getLogger(__name__)

FNCER = ("solar", "eolica")
TIPO_FNCER = {"solar": "SOLAR", "eolica": "EOLICA"}

# tCO2e/MWh por combustible, solo si XM no publica emisiones para el recurso en el periodo.
FACTOR_CO2_DEFECTO = {"GAS": 0.50, "CARBON": 0.95, "ACPM": 0.70, "COMBUSTOLEO": 0.75,
                      "JET-A1": 0.70, "GLP": 0.60}


@dataclass
class Insumos:
    tiempo: pd.DatetimeIndex            # inicio de cada hora del periodo (P)
    recursos: pd.DataFrame              # índice = código, R filas
    costo: np.ndarray                   # (R,P) COP/kWh, precio de oferta ideal
    disp: np.ndarray                    # (R,P) MW, disponibilidad comercial
    inflex: np.ndarray                  # (R,P) MW, generación inflexible (piso)
    demanda: np.ndarray                 # (P,) MW, demanda comercial del SIN
    fijo: pd.DataFrame                  # (P × tipo) MW de recursos sin oferta
    fijo_co2: np.ndarray                # (P,) tCO2e de los recursos sin oferta
    gen_ideal: np.ndarray               # (R,P) MW, generación ideal publicada por XM
    precio_marginal_xm: np.ndarray      # (P,) MaxPrecOferNal
    precio_bolsa_xm: np.ndarray         # (P,) PrecBolsNaci
    delta_i: np.ndarray                 # (P,) PrecBolsNaci - MaxPrecOferNal
    marginal_xm: np.ndarray             # (P,) código del recurso marginal según XM
    req_agc: np.ndarray                 # (P,) MW de holgura AGC programada
    cf: dict[str, np.ndarray]           # perfiles de factor de planta FNCER (P,)
    fncer_existente: dict[str, np.ndarray]  # (P,) MW generados por solar/eólica existentes
    precio_fncer_ref: float             # mediana de las ofertas solares del periodo
    capacidad_fncer_existente: dict[str, float] = field(default_factory=dict)
    real: pd.DataFrame | None = None          # (P × categoría) MW de generación real (Gene)
    real_por_recurso: pd.DataFrame | None = None  # (P × código) MW de generación real (Gene)
    inercia_real: np.ndarray | None = None    # (P,) GW·s estimada con la generación real
    inercia_fija: np.ndarray | None = None    # (P,) GW·s de los recursos sin oferta (GeneIdea)
    factor_potencia: float = 0.9

    @property
    def R(self) -> int:
        return len(self.recursos)

    @property
    def P(self) -> int:
        return len(self.tiempo)

    @property
    def dias(self) -> int:
        return self.P // 24

    def residual(self) -> np.ndarray:
        """Demanda que deben cubrir los recursos con oferta."""
        return self.demanda - self.fijo.sum(axis=1).to_numpy()


def _tiempo(ini, fin) -> pd.DatetimeIndex:
    return pd.date_range(pd.Timestamp(ini), pd.Timestamp(fin) + pd.Timedelta(hours=23), freq="h")


def _clave(df: pd.DataFrame) -> pd.Series:
    return df["fecha"] + pd.to_timedelta(df["hora"] - 1, unit="h")


def _matriz(df: pd.DataFrame, codigos, tiempo, escala=1.0, relleno=np.nan) -> np.ndarray:
    """Formato largo horario -> matriz (códigos × tiempo)."""
    if df.empty:
        return np.full((len(codigos), len(tiempo)), relleno)
    w = (df.assign(t=_clave(df))
           .pivot_table(index="codigo", columns="t", values="valor", aggfunc="sum"))
    w = w.reindex(index=list(codigos), columns=tiempo)
    return w.to_numpy(dtype=float) * escala if relleno is np.nan else \
        w.fillna(relleno).to_numpy(dtype=float) * escala


def _sistema(df: pd.DataFrame, tiempo, escala=1.0) -> np.ndarray:
    return _matriz(df, ["Sistema"], tiempo, escala)[0]


def _diaria_a_horaria(df: pd.DataFrame, codigos, tiempo) -> np.ndarray:
    """Métrica diaria por recurso repetida en las 24 horas de cada día."""
    if df.empty:
        return np.full((len(codigos), len(tiempo)), np.nan)
    w = df.pivot_table(index="codigo", columns="fecha", values="valor", aggfunc="mean")
    w = w.reindex(index=list(codigos), columns=tiempo.normalize().unique())
    return np.repeat(w.to_numpy(dtype=float), 24, axis=1)


def _perfil_flota(gen: pd.DataFrame, cap: pd.DataFrame, codigos, tiempo,
                  umbral_activo=0.10) -> tuple[np.ndarray, float]:
    """Factor de planta horario de una flota = Σ generación / Σ capacidad de los recursos activos.

    Un recurso cuenta como activo un día si su pico de ese día supera `umbral_activo` de su
    capacidad (así se excluyen plantas en mantenimiento o recién conectadas). Si XM no publica
    capacidad efectiva para el recurso, se usa su máxima generación horaria observada.
    """
    G = np.nan_to_num(_matriz(gen, codigos, tiempo, escala=1e-3))
    C = _diaria_a_horaria(cap, codigos, tiempo) * 1e-3
    pico = np.nanmax(np.where(G > 0, G, np.nan), axis=1, initial=0.0)
    C = np.where(np.isnan(C), pico[:, None], C)
    dias = len(tiempo) // 24
    pico_dia = G.reshape(len(codigos), dias, 24).max(axis=2)
    activo = np.repeat(pico_dia > umbral_activo * C.reshape(len(codigos), dias, 24)[:, :, 0], 24, axis=1)
    cap_activa = (C * activo).sum(axis=0)
    cf = np.divide((G * activo).sum(axis=0), cap_activa, out=np.zeros(len(tiempo)), where=cap_activa > 0)
    return np.clip(cf, 0.0, 1.0), float(np.nanmean(cap_activa))


def construir_insumos(cfg: dict) -> Insumos:
    ini, fin = cfg["periodo"]["inicio"], cfg["periodo"]["fin"]
    xm = FuenteXM(cfg["datos"]["cache"])
    simem = FuenteSIMEM(cfg["datos"]["cache"], cfg["datos"]["version_simem"])
    tiempo = _tiempo(ini, fin)
    P = len(tiempo)

    # --- Datos crudos ------------------------------------------------------------------------
    lst = xm.listado_recursos().set_index("codigo")
    s = lambda m, e: xm.serie(m, e, ini, fin)  # noqa: E731
    oferta_ideal, oferta_desp = s("PrecOferIdeal", "Recurso"), s("PrecOferDesp", "Recurso")
    dispo, gideal, gene = s("DispoCome", "Recurso"), s("GeneIdea", "Recurso"), s("Gene", "Recurso")
    cap_efec, emis = s("CapEfecNeta", "Recurso"), s("EmisionesCO2Eq", "Recurso")
    marg = s("IndRecMargina", "Recurso")
    dem, pbolsa, pmax = s("DemaCome", "Sistema"), s("PrecBolsNaci", "Sistema"), s("MaxPrecOferNal", "Sistema")
    holgura = simem.serie("897043", ini, fin, variable="HOArribaG")
    inflex = simem.serie("FE1E73", ini, fin, variable="InflexRecursoProcComercial")
    unidades = simem.tabla("670221", ini, fin)

    # --- Recursos con oferta ------------------------------------------------------------------
    codigos = sorted(set(oferta_ideal["codigo"]) | set(oferta_desp["codigo"]))
    costo = _diaria_a_horaria(oferta_ideal, codigos, tiempo)
    costo_desp = _matriz(oferta_desp, codigos, tiempo)
    costo = np.where(np.isnan(costo), costo_desp, costo)
    ofertado = ~np.isnan(costo)

    disp = np.nan_to_num(_matriz(dispo, codigos, tiempo, escala=1e-3))
    disp = np.where(ofertado, np.maximum(disp, 0.0), 0.0)
    costo = np.nan_to_num(costo)
    piso = np.nan_to_num(_matriz(inflex, codigos, tiempo, escala=1e-3))
    piso = np.clip(piso, 0.0, disp)

    rec = pd.DataFrame(index=pd.Index(codigos, name="codigo"))
    for c in ["nombre", "tipo", "fuente", "clase", "despacho"]:
        rec[c] = lst[c].reindex(codigos).fillna("DESCONOCIDO").to_numpy()
    rec["agc"] = rec.index.isin(set(holgura.loc[holgura["valor"] > 0, "codigo"]))
    rec["factor_co2"] = _factores_co2(emis, gene, rec.index, rec["fuente"])

    # --- Máquinas (para la inercia) -------------------------------------------------------------
    cfg_in = cfg["inercia"]
    reales = sorted(set(gene["codigo"]))
    cap_mw = cap_efec.groupby("codigo")["valor"].mean() * 1e-3
    maq = tabla_maquinas(sorted(set(codigos) | set(reales) | set(gideal["codigo"])), lst["tipo"],
                         cap_mw, unidades, cfg_in["H_s"])
    rec = rec.join(maq[["H_s", "cap_mw", "n_unidades"]])
    rec["cap_mw"] = rec["cap_mw"].fillna(pd.Series(disp.max(axis=1), index=rec.index))

    # --- Recursos sin oferta: inyección fija --------------------------------------------------
    todos = sorted(set(gideal["codigo"]))
    GI_todos = np.nan_to_num(_matriz(gideal, todos, tiempo, escala=1e-3))
    pos = {c: i for i, c in enumerate(codigos)}
    sin_oferta = np.ones_like(GI_todos, dtype=bool)
    for j, c in enumerate(todos):
        if c in pos:
            sin_oferta[j] = ~ofertado[pos[c]]
    G_fijo = np.where(sin_oferta, GI_todos, 0.0)
    tipo_todos = lst["tipo"].reindex(todos).fillna("OTRO").to_numpy()
    fijo = pd.DataFrame(G_fijo.T, index=tiempo, columns=tipo_todos).T.groupby(level=0).sum().T
    f_todos = _factores_co2(emis, gene, todos, lst["fuente"].reindex(todos).fillna(""))
    fijo_co2 = (G_fijo * f_todos.to_numpy()[:, None]).sum(axis=0)

    gen_ideal = np.nan_to_num(_matriz(gideal, codigos, tiempo, escala=1e-3))

    # --- Comportamiento real del SIN (Gene) e inercia estimada ---------------------------------
    fp = cfg_in["factor_potencia"]
    G_real = np.nan_to_num(_matriz(gene, reales, tiempo, escala=1e-3))
    cats = [categoria(*(lst[c].get(r, "") for c in ("tipo", "despacho", "clase"))) for r in reales]
    real = pd.DataFrame(G_real.T, index=tiempo, columns=cats).T.groupby(level=0).sum().T
    m = maq.reindex(reales)
    inercia_real = energia_cinetica(G_real, m["H_s"], m["cap_mw"], m["n_unidades"], fp)
    m = maq.reindex(todos)
    inercia_fija = energia_cinetica(G_fijo, m["H_s"], m["cap_mw"], m["n_unidades"], fp)

    # --- Sistema y referencias ----------------------------------------------------------------
    demanda = _sistema(dem, tiempo, escala=1e-3)
    p_bolsa, p_max = _sistema(pbolsa, tiempo), _sistema(pmax, tiempo)
    delta_i = p_bolsa - p_max
    delta_i = np.where(np.isnan(delta_i), np.nanmedian(delta_i), delta_i)
    marginal = np.full(P, "", dtype=object)
    if not marg.empty:
        m = marg[marg["valor"] > 0].assign(t=_clave)
        m = m.sort_values("codigo").drop_duplicates("t").set_index("t")["codigo"]
        marginal = m.reindex(tiempo).fillna("").to_numpy(dtype=object)

    req = _sistema(holgura.assign(codigo="Sistema"), tiempo, escala=1e-3) if not holgura.empty \
        else np.full(P, np.nan)
    req = np.where(np.isnan(req), np.nanmedian(req) if np.isfinite(req).any() else 0.0, req)

    # --- Perfiles y FNCER existente -----------------------------------------------------------
    perfiles = cfg["fncer"]["perfiles"]
    con_generacion = set(gene["codigo"].unique())
    cf, cap_exist, existente = {}, {}, {}
    for k in FNCER:
        sel = perfiles.get(k, "auto")
        cods = (lst.index[lst["tipo"] == TIPO_FNCER[k]].tolist() if sel == "auto" else list(sel))
        cods = [c for c in cods if c in con_generacion]
        if cods:
            cf[k], cap_exist[k] = _perfil_flota(gene[gene["codigo"].isin(cods)],
                                                cap_efec[cap_efec["codigo"].isin(cods)], cods, tiempo)
        else:
            log.warning("Sin datos para el perfil %s: se usa factor de planta cero.", k)
            cf[k], cap_exist[k] = np.zeros(P), 0.0
        es_k = (rec["tipo"] == TIPO_FNCER[k]).to_numpy()
        existente[k] = gen_ideal[es_k].sum(axis=0) + \
            (fijo[TIPO_FNCER[k]].to_numpy() if TIPO_FNCER[k] in fijo else 0.0)

    solares = (rec["tipo"] == "SOLAR").to_numpy()
    ofertas_solares = costo[solares][ofertado[solares]]
    precio_ref = float(np.median(ofertas_solares)) if ofertas_solares.size else 0.0

    ins = Insumos(tiempo=tiempo, recursos=rec, costo=costo, disp=disp, inflex=piso, demanda=demanda,
                  fijo=fijo, fijo_co2=fijo_co2, gen_ideal=gen_ideal, precio_marginal_xm=p_max,
                  precio_bolsa_xm=p_bolsa, delta_i=delta_i, marginal_xm=marginal, req_agc=req,
                  cf=cf, fncer_existente=existente, precio_fncer_ref=precio_ref,
                  capacidad_fncer_existente=cap_exist, real=real, inercia_real=inercia_real,
                  real_por_recurso=pd.DataFrame(G_real.T, index=tiempo, columns=reales),
                  inercia_fija=inercia_fija, factor_potencia=fp)
    _chequeos(ins)
    return ins


def categoria(tipo: str, despacho: str, clase: str) -> str:
    """Categoría de un recurso según su tipo, si lo despacha XM y su clase en ListadoRecursos."""
    central = despacho == "DESPACHADO CENTRALMENTE"
    if tipo == "HIDRAULICA":
        return "Hidráulica despachada" if central else "Hidráulica no despachada"
    if tipo == "SOLAR":
        if central:
            return "Solar despachada"
        return {"AUTOG PEQ. ESCALA": "Solar AGPE", "GEN. DISTRIBUIDA": "Solar GD",
                "NORMAL": "Solar planta menor"}.get(clase, "Solar autogenerador")
    return {"TERMICA": "Térmica", "COGENERADOR": "Cogeneración", "EOLICA": "Eólica"}.get(tipo, "Otros")


def _factores_co2(emis: pd.DataFrame, gene: pd.DataFrame, codigos, fuentes) -> pd.Series:
    """tCO2e/MWh = Σ emisiones horarias publicadas (tCO2e) / Σ generación real (MWh)."""
    e = emis.groupby("codigo")["valor"].sum()
    g = gene.groupby("codigo")["valor"].sum() * 1e-3
    f = (e / g.where(g > 0)).reindex(list(codigos))
    defecto = pd.Series(fuentes, index=list(codigos)).map(FACTOR_CO2_DEFECTO).fillna(0.0)
    f = f.where(f.notna() & (f < 2.0), defecto)
    return f.astype(float)


def _chequeos(ins: Insumos) -> None:
    res = ins.residual()
    holgura = ins.disp.sum(axis=0) - res
    if (holgura < 0).any():
        log.warning("%d horas con disponibilidad ofertada menor a la demanda residual.",
                    int((holgura < 0).sum()))
    if np.isnan(ins.demanda).any():
        raise ValueError("Faltan datos de demanda (DemaCome) en el periodo.")
    log.info("Insumos: %d recursos con oferta, %d horas, demanda media %.0f MW, residual %.0f MW",
             ins.R, ins.P, ins.demanda.mean(), res.mean())

"""Gráficos PNG del reporte.

Paleta validada con scripts/validate_palette.js de la guía dataviz (modo claro): pasa separación
para daltonismo y visión normal. Aqua y amarillo quedan bajo 3:1 de contraste con el fondo, por eso
cada gráfico lleva leyenda y todos los valores están también en tablas (Excel y Markdown).
Colores por entidad, iguales en todos los gráficos:
  tecnologías  hidráulica=azul, térmica=naranja, eólica=aqua, solar=amarillo (no despachada con trama)
  escenarios   base=azul, sin FNCER=naranja, +eólica=aqua, +solar=amarillo, real XM=gris oscuro
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .modelo import Resultado  # noqa: E402

SUPERFICIE, TINTA, TINTA_2, REJILLA, NEUTRO = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e0", "#b5b4ad"
AZUL, NARANJA, AQUA, AMARILLO = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
RAMPA_AZUL = ["#86b6ef", "#2a78d6", "#104281"]  # ordinal, validada con --ordinal

COLOR_TECNOLOGIA = {"Hidráulica": AZUL, "Térmica": NARANJA, "Otros sincrónicos": NEUTRO, "Otros": NEUTRO,
                    "Eólica": AQUA, "Solar": AMARILLO, "Solar despachada": AMARILLO,
                    "Solar no despachada": AMARILLO}
TRAMA = {"Solar no despachada": "////"}

ESCENARIOS_GRAFICO = [("base", "Base (modelo)", AZUL), ("sin_fncer", "Sin solar/eólica existentes", NARANJA),
                      ("eolica_+1000", "+1.000 MW eólica", AQUA), ("solar_+3000", "+3.000 MW solar", AMARILLO)]

plt.rcParams.update({
    "figure.facecolor": SUPERFICIE, "axes.facecolor": SUPERFICIE, "savefig.facecolor": SUPERFICIE,
    "axes.edgecolor": REJILLA, "axes.labelcolor": TINTA_2, "axes.titlecolor": TINTA,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.labelsize": 9, "xtick.color": TINTA_2, "ytick.color": TINTA_2,
    "xtick.labelsize": 8, "ytick.labelsize": 8, "axes.grid": True, "grid.color": REJILLA,
    "grid.linewidth": 0.6, "grid.linestyle": "-", "axes.spines.top": False,
    "axes.spines.right": False, "axes.spines.left": False, "legend.frameon": False,
    "legend.fontsize": 8, "font.size": 9, "lines.linewidth": 1.6, "axes.axisbelow": True,
    "hatch.color": SUPERFICIE, "hatch.linewidth": 1.2,
})

HORAS = np.arange(1, 25)


def _subtitulo(ax, texto):
    ax.text(0, 1.02, texto, transform=ax.transAxes, fontsize=8.5, color=TINTA_2, va="bottom")


def _guardar(fig, ruta: Path) -> Path:
    fig.savefig(ruta, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return ruta


def _eje_horas(ax):
    ax.set_xticks([1, 4, 8, 12, 16, 20, 24])
    ax.set_xlim(1, 24)
    ax.set_xlabel("Hora del día (hora 1 = 00:00–01:00)")


def _miles(x):
    return f"{x:,.0f}".replace(",", ".")


def _perfil(r: Resultado, valores) -> np.ndarray:
    return pd.Series(valores, index=r.ins.tiempo).groupby(r.ins.tiempo.hour).mean().to_numpy()


# --------------------------------------------------------------------------------------------
# Sección 2-4: comportamiento real del SIN
# --------------------------------------------------------------------------------------------
def sin_perfil_horario(perfil: pd.DataFrame, ruta: Path, titulo_periodo: str) -> Path:
    grupos = ["Hidráulica", "Térmica", "Otros sincrónicos", "Eólica", "Solar despachada", "Solar no despachada"]
    fig, ax = plt.subplots(figsize=(10, 4.6))
    base = np.zeros(24)
    for g in grupos:
        v = perfil[f"{g}_MW"].to_numpy()
        ax.fill_between(HORAS, base, base + v, facecolor=COLOR_TECNOLOGIA[g], hatch=TRAMA.get(g),
                        hatchcolor=SUPERFICIE, edgecolor=SUPERFICIE, linewidth=0.8, label=g)
        base = base + v
    ax.plot(HORAS, perfil["demanda_MW"], color=TINTA, lw=1.6, label="Demanda")
    _eje_horas(ax)
    ax.set_ylabel("MW (promedio de todos los días)")
    ax.set_ylim(0, perfil["demanda_MW"].max() * 1.08)
    ax.set_title(f"Generación real del SIN por hora del día · {titulo_periodo}", pad=18)
    _subtitulo(ax, "Generación medida por XM (Gene). La solar no despachada lleva trama.")
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.13), ncol=7, handlelength=1.6)
    return _guardar(fig, ruta)


def demanda_neta(serie: pd.DataFrame, perfil: pd.DataFrame, ind: dict, ruta: Path) -> Path:
    fig, ax = plt.subplots(figsize=(10, 4.6))
    neta_dia = serie["demanda_neta_MW"].groupby([serie.index.normalize(), serie.index.hour]).mean().unstack()
    ax.fill_between(HORAS, neta_dia.quantile(0.1).to_numpy(), neta_dia.quantile(0.9).to_numpy(),
                    color=NARANJA, alpha=0.14, linewidth=0, label="Demanda neta: 80 % de los días")
    ax.plot(HORAS, perfil["demanda_MW"], color=AZUL, lw=2, label="Demanda (DemaCome)")
    ax.plot(HORAS, perfil["residual_central_MW"], color=AQUA, lw=2,
            label="Residual para el despacho central (demanda − recursos sin oferta)")
    ax.plot(HORAS, perfil["demanda_neta_MW"], color=NARANJA, lw=2, label="Demanda neta (demanda − solar − eólica)")
    h = ind["hora_pico_solar_promedio"]
    d, n = perfil.loc[h, "demanda_MW"], perfil.loc[h, "demanda_neta_MW"]
    ax.annotate("", xy=(h, n), xytext=(h, d), arrowprops=dict(arrowstyle="<->", color=TINTA_2, lw=0.9))
    ax.annotate(f"Solar ≈ {_miles(ind['solar_total_en_pico_MW'])} MW\n"
                f"({ind['solar_en_pico_pct_demanda']:.0f} % de la demanda)", xy=(h, (d + n) / 2),
                xytext=(8, 0), textcoords="offset points", va="center", fontsize=8, color=TINTA)
    hr = ind["rampa_tarde_promedio_hora"]
    ax.annotate(f"Rampa media +{_miles(ind['rampa_tarde_promedio_MW_h'])} MW/h\n"
                f"(las hidráulicas cubren {ind['subida_tarde_hidraulica_pct']:.0f} % de la subida)",
                xy=(hr - 0.5, perfil["demanda_neta_MW"].loc[hr - 1:hr].mean()), xytext=(-70, 22),
                textcoords="offset points", ha="right", va="bottom", fontsize=8, color=TINTA,
                arrowprops=dict(arrowstyle="-", color=TINTA_2, lw=0.8))
    _eje_horas(ax)
    ax.set_ylabel("MW (promedio por hora)")
    ax.set_title("Demanda frente a demanda neta: el efecto de la solar al mediodía", pad=18)
    _subtitulo(ax, "La solar abre un valle al mediodía; al caer el sol la demanda neta sube rápido hacia el pico de la noche")
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.13), ncol=2)
    return _guardar(fig, ruta)


def inercia(serie: pd.DataFrame, resultados: dict, ruta: Path) -> Path:
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.3))
    real_E = serie["inercia_GWs"].groupby(serie.index.hour).mean().to_numpy()
    real_ns = serie["no_sincronica_pct"].groupby(serie.index.hour).mean().to_numpy()
    axs[0].plot(HORAS, real_E, color=TINTA_2, lw=2, label="Operación real XM (estimada)")
    axs[1].plot(HORAS, real_ns, color=TINTA_2, lw=2, label="Operación real XM")
    for nombre, etiqueta, color in ESCENARIOS_GRAFICO:
        if nombre not in resultados:
            continue
        r = resultados[nombre][0]
        axs[0].plot(HORAS, _perfil(r, r.inercia()), color=color, lw=2, label=etiqueta)
        axs[1].plot(HORAS, _perfil(r, 100 * r.snsp()), color=color, lw=2, label=etiqueta)
    axs[0].set(ylabel="GW·s")
    axs[0].set_title("Inercia sincrónica estimada", pad=18)
    _subtitulo(axs[0], "Energía cinética de las unidades sincrónicas en línea (promedio por hora)")
    axs[1].set(ylabel="% de la demanda")
    axs[1].set_title("Generación no sincrónica (solar + eólica)", pad=18)
    _subtitulo(axs[1], "Proporción de la demanda cubierta con inversores")
    for ax in axs:
        _eje_horas(ax)
    axs[0].set_ylim(bottom=0)
    axs[1].legend(loc="upper left")
    return _guardar(fig, ruta)


# --------------------------------------------------------------------------------------------
# Sección 5: validación
# --------------------------------------------------------------------------------------------
def validacion_precio(res: Resultado, metr: dict, ruta: Path) -> Path:
    ins = res.ins
    fig, ax = plt.subplots(figsize=(11, 3.8))
    ax.plot(ins.tiempo, ins.precio_marginal_xm, color=AZUL, lw=1.2, label="XM (MaxPrecOferNal)")
    ax.plot(ins.tiempo, res.precio, color=NARANJA, lw=1.0, label="Modelo LP")
    ax.set_ylabel("COP/kWh")
    ax.set_title("Precio marginal horario: modelo frente a XM", pad=18)
    _subtitulo(ax, f"Error medio {100 * metr['mape_precio']:.1f} % · precio exacto en "
                   f"{100 * metr['horas_precio_exacto_pct']:.0f} % de las horas · correlación "
                   f"{metr['correlacion_precio']:.2f}")
    ax.legend(loc="lower right", ncol=2, bbox_to_anchor=(1, 1.0))
    ax.margins(x=0.01)
    return _guardar(fig, ruta)


def validacion_recursos(res: Resultado, ruta: Path, n_etiquetas: int = 5) -> Path:
    ins = res.ins
    xm, mod = ins.gen_ideal.sum(axis=1) / 1e3, res.g.sum(axis=1) / 1e3
    fig, ax = plt.subplots(figsize=(5.2, 5))
    lim = max(xm.max(), mod.max()) * 1.05
    ax.plot([0, lim], [0, lim], color=NEUTRO, lw=1, zorder=1)
    ax.scatter(xm, mod, s=26, color=AZUL, edgecolor=SUPERFICIE, linewidth=1.2, zorder=2)
    # Las plantas con mayor diferencia: las sobreestimadas a la izquierda de la diagonal y las
    # subestimadas a la derecha, escalonadas para que no se encimen las que están cerca.
    sel = np.argsort(-np.abs(mod - xm))[:n_etiquetas]
    for lado, signo in (("arriba", 1), ("abajo", -1)):
        grupo = [i for i in sel if (mod[i] >= xm[i]) == (lado == "arriba")]
        for k, i in enumerate(sorted(grupo, key=lambda j: mod[j] * signo)):
            dx, dy = (-6, 4 + 11 * k) if signo > 0 else (6, -10 - 11 * k)
            ax.annotate(ins.recursos.index[i], (xm[i], mod[i]), xytext=(dx, dy), textcoords="offset points",
                        ha="right" if signo > 0 else "left", fontsize=7.5, color=TINTA_2,
                        arrowprops=dict(arrowstyle="-", color=NEUTRO, lw=0.6) if k else None)
    ax.set(xlim=(0, lim), ylim=(0, lim), xlabel="Generación ideal XM (GWh)", ylabel="Modelo (GWh)")
    ax.set_title("Energía por recurso en el periodo", pad=18)
    _subtitulo(ax, "Cada punto es una planta; la diagonal es coincidencia exacta")
    return _guardar(fig, ruta)


# --------------------------------------------------------------------------------------------
# Sección 6-7: escenarios
# --------------------------------------------------------------------------------------------
def precio_por_hora(resultados: dict, ruta: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    for nombre, etiqueta, color in ESCENARIOS_GRAFICO:
        if nombre in resultados:
            r = resultados[nombre][0]
            ax.plot(HORAS, _perfil(r, r.precio_bolsa), color=color, lw=2, label=etiqueta)
    _eje_horas(ax)
    ax.set_ylabel("COP/kWh")
    ax.set_title("Precio de bolsa medio por hora del día", pad=18)
    _subtitulo(ax, "La solar baja el precio a mediodía; la eólica también en la punta de la noche")
    ax.legend(loc="upper left")
    return _guardar(fig, ruta)


def _mix(tabla: pd.DataFrame) -> pd.DataFrame:
    g = lambda c: tabla.get(c, 0.0)  # noqa: E731
    mix = pd.DataFrame({
        "Hidráulica": g("gen_HIDRAULICA_GWh"), "Térmica": g("gen_TERMICA_GWh"),
        "Eólica": g("gen_EOLICA_GWh") + g("gen_EOLICA_NUEVA_GWh"),
        "Solar": g("gen_SOLAR_GWh") + g("gen_SOLAR_NUEVA_GWh"),
    })
    mix["Otros"] = tabla["demanda_GWh"] - mix.sum(axis=1)
    mix.index = tabla["escenario"]
    return mix.clip(lower=0)


def mezcla(tabla: pd.DataFrame, ruta: Path) -> Path:
    mix = _mix(tabla).iloc[::-1]
    fig, ax = plt.subplots(figsize=(9, 0.5 * len(mix) + 1.6))
    izquierda = np.zeros(len(mix))
    for tec in mix.columns:
        ax.barh(mix.index, mix[tec], left=izquierda, color=COLOR_TECNOLOGIA[tec], height=0.62,
                edgecolor=SUPERFICIE, linewidth=1.5, label=tec)
        izquierda += mix[tec].to_numpy()
    ax.set_xlabel("GWh en el periodo")
    ax.grid(axis="y", visible=False)
    ax.set_title("Mezcla de generación por escenario (despacho ideal)", pad=26)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=5)
    return _guardar(fig, ruta)


def impacto(tabla: pd.DataFrame, ruta: Path) -> Path:
    t = tabla[tabla["escenario"] != "base"].iloc[::-1]
    paneles = [("delta_precio_pct", "Precio de bolsa ponderado"), ("delta_emisiones_pct", "Emisiones de CO2e")]
    fig, axs = plt.subplots(1, 2, figsize=(11, 0.45 * len(t) + 1.8), sharey=True)
    for ax, (col, titulo) in zip(axs, paneles):
        v = t[col].to_numpy()
        ax.barh(t["escenario"], v, color=AZUL, height=0.6)
        ax.axvline(0, color=TINTA_2, lw=0.8)
        for y, x in enumerate(v):
            ax.annotate(f"{x:+.1f} %", (x, y), xytext=(4 if x >= 0 else -4, 0), textcoords="offset points",
                        ha="left" if x >= 0 else "right", va="center", fontsize=8, color=TINTA)
        ax.grid(axis="y", visible=False)
        ax.set_title(titulo, pad=18)
        _subtitulo(ax, "Cambio frente al escenario base (%)")
        lo, hi = min(v.min(), 0), max(v.max(), 0)
        ax.set_xlim(lo - 0.25 * (hi - lo) - 1, hi + 0.25 * (hi - lo) + 1)
    return _guardar(fig, ruta)


def barrido(barr: pd.DataFrame, ruta: Path) -> Path:
    paneles = [("precio_bolsa_ponderado_COP_kWh", "Precio de bolsa ponderado", "COP/kWh"),
               ("reserva_req_media_MW", "Reserva AGC requerida (media)", "MW"),
               ("inercia_min_GWs", "Inercia mínima del mes", "GW·s")]
    niveles = sorted(barr["eolica_nueva_MW"].unique())
    colores = RAMPA_AZUL[-len(niveles):] if len(niveles) <= len(RAMPA_AZUL) else RAMPA_AZUL
    fig, axs = plt.subplots(1, 3, figsize=(13, 3.9))
    for ax, (col, titulo, unidad) in zip(axs, paneles):
        for nivel, color in zip(niveles, colores):
            d = barr[barr["eolica_nueva_MW"] == nivel].sort_values("solar_nueva_MW")
            ax.plot(d["solar_nueva_MW"], d[col], color=color, lw=2, marker="o", ms=4, label=f"{_miles(nivel)} MW")
        ax.set(xlabel="Solar nueva (MW)", ylabel=unidad)
        ax.set_title(titulo, pad=8)
    axs[0].legend(title="Eólica nueva", loc="best", title_fontsize=8)
    return _guardar(fig, ruta)

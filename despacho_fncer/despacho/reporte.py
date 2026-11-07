"""Reporte organizado por secciones: Excel, Markdown con gráficos y CSV para PowerFactory.

Orden de las secciones (igual en el Markdown y en las hojas del Excel):
  1. Datos usados: días, resolución horaria, fuentes.
  2. Comportamiento real del SIN: perfil por hora, tabla diaria, real frente a despacho ideal.
  3. Demanda y solar del mediodía: qué muestran los datos y cómo lo trata el modelo.
  4. Inercia y generación no sincrónica.
  5. Validación del modelo.
  6. Escenarios.   7. Entrada óptima.   8. Sensibilidad de la reserva.   9. Supuestos.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import graficos as gr
from .modelo import Resultado

log = logging.getLogger(__name__)

FUENTES = [
    ("Demanda", "DemaCome (Sistema)", "API XM", "horaria", "kWh", "Demanda que se debe atender cada hora"),
    ("Generación real", "Gene (Recurso)", "API XM", "horaria", "kWh",
     "Comportamiento real, perfiles solar y eólico, factor de CO2, inercia real"),
    ("Generación ideal", "GeneIdea (Recurso)", "API XM", "horaria", "kWh",
     "Inyección fija de los recursos sin oferta y referencia de validación"),
    ("Precio de oferta", "PrecOferIdeal (respaldo PrecOferDesp)", "API XM", "diaria", "COP/kWh", "Costo de cada planta en el LP"),
    ("Disponibilidad", "DispoCome (Recurso)", "API XM", "horaria", "kW", "Límite superior de cada planta"),
    ("Precios de referencia", "MaxPrecOferNal, PrecBolsNaci", "API XM", "horaria", "COP/kWh", "Validación del precio y ΔI"),
    ("Recurso marginal", "IndRecMargina (Recurso)", "API XM", "horaria", "indicador", "Validación"),
    ("Capacidad", "CapEfecNeta (Recurso)", "API XM", "diaria", "kW", "Perfiles FNCER y tamaño de unidad"),
    ("Emisiones", "EmisionesCO2Eq (Recurso)", "API XM", "horaria", "tCO2e", "Factor de emisión por planta"),
    ("Operación real", "GeneSeguridad, GeneFueraMerito", "API XM", "horaria", "kWh", "Explicar real frente a ideal"),
    ("Atributos", "ListadoRecursos", "API XM", "listado", "-", "Tipo, despacho central y clase (AGPE, GD, menor)"),
    ("Reserva AGC", "Holgura AGC (897043, HOArribaG)", "SIMEM", "horaria", "kWh", "Requerimiento base y plantas AGC"),
    ("Inflexibilidades", "Inflexibilidad proceso comercial (FE1E73)", "SIMEM", "horaria", "kWh", "Piso de generación"),
    ("Unidades", "Listado de unidades de generación (670221)", "SIMEM", "diaria", "-", "Unidades por planta (inercia)"),
]


def _h(h: int) -> str:
    return f"hora {h} ({h - 1:02d}:00–{h % 24:02d}:00)"


def _f(x, d=0):
    if isinstance(x, (float, np.floating)):
        return "" if np.isnan(x) else f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(x)


def _md_tabla(df: pd.DataFrame, decimales: int | dict = 1) -> str:
    cols = list(df.columns)
    dec = decimales if isinstance(decimales, dict) else {c: decimales for c in cols}
    filas = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for fila in df.itertuples(index=False):
        filas.append("| " + " | ".join(_f(v, dec.get(c, 1)) for c, v in zip(cols, fila)) + " |")
    return "\n".join(filas)


def _calendario(ins) -> dict:
    dias = ins.tiempo.normalize().unique()
    sem = pd.Series(dias.weekday)
    return {"inicio": dias[0].date(), "fin": dias[-1].date(), "dias": len(dias), "horas": ins.P,
            "laborables": int((sem < 5).sum()), "sabados": int((sem == 5).sum()), "domingos": int((sem == 6).sum())}


# --------------------------------------------------------------------------------------------
# Exportaciones
# --------------------------------------------------------------------------------------------
def exportar_powerfactory(res: Resultado, carpeta: Path) -> Path:
    """Despacho horario por planta en MW (filas = hora, columnas = código XM)."""
    carpeta.mkdir(parents=True, exist_ok=True)
    largo = res.despacho_por_recurso()
    ancho = largo.pivot_table(index="fecha_hora", columns="codigo", values="MW", aggfunc="sum").fillna(0.0)
    ruta = carpeta / f"despacho_modelo_{res.escenario.nombre}.csv"
    ancho.round(3).to_csv(ruta)
    return ruta


def exportar_real(ins, lst: pd.DataFrame, carpeta: Path) -> Path:
    """Generación real medida por XM por planta (todas, incluidas las no despachadas)."""
    carpeta.mkdir(parents=True, exist_ok=True)
    g = ins.real_por_recurso
    g = g.loc[:, g.sum() > 0]
    g.index.name = "fecha_hora"
    ruta = carpeta / "generacion_real_XM.csv"
    g.round(3).to_csv(ruta)
    rec = lst.reindex(g.columns)[["nombre", "tipo", "despacho", "clase"]]
    rec.index.name = "codigo"
    rec.to_csv(carpeta / "recursos.csv")
    return ruta


# --------------------------------------------------------------------------------------------
# Reporte
# --------------------------------------------------------------------------------------------
def generar_reporte(cfg: dict, c: dict) -> dict:
    """`c` reúne todo lo calculado en run.py (ver claves usadas abajo)."""
    salida = Path(cfg["salidas"]["carpeta"])
    graf = salida / "graficos"
    graf.mkdir(parents=True, exist_ok=True)
    for f in graf.glob("*.png"):
        f.unlink()
    ins, serie, perfil = c["ins"], c["serie_real"], c["perfil"]
    res_val, metr, tec, rec_val = c["val"]
    cal = _calendario(ins)
    periodo = f"{cal['inicio']:%d/%m/%Y} a {cal['fin']:%d/%m/%Y}"

    pngs = {
        "sin_perfil": gr.sin_perfil_horario(perfil, graf / "01_sin_generacion_por_hora.png", periodo),
        "demanda_neta": gr.demanda_neta(serie, perfil, c["ind_sin"], graf / "02_demanda_neta_mediodia.png"),
        "inercia": gr.inercia(serie, c["resultados"], graf / "03_inercia_y_no_sincronica.png"),
        "val_precio": gr.validacion_precio(res_val, metr, graf / "04_validacion_precio.png"),
        "val_recursos": gr.validacion_recursos(res_val, graf / "05_validacion_recursos.png"),
        "precio_hora": gr.precio_por_hora(c["resultados"], graf / "06_escenarios_precio_por_hora.png"),
        "impacto": gr.impacto(c["tabla"], graf / "07_escenarios_impacto.png"),
        "mezcla": gr.mezcla(c["tabla"], graf / "08_escenarios_mezcla.png"),
    }
    if c.get("barrido") is not None and not c["barrido"].empty:
        pngs["barrido"] = gr.barrido(c["barrido"], graf / "09_barrido_capacidades.png")

    excel = salida / "resultados_despacho.xlsx"
    _excel(excel, cfg, c, cal, pngs)

    pf = salida / "powerfactory"
    exportados = [exportar_real(ins, c["listado"], pf)]
    exportados += [exportar_powerfactory(c["resultados"][n][0], pf)
                   for n in cfg["salidas"].get("exportar_despacho", []) if n in c["resultados"]]

    md = salida / "reporte.md"
    md.write_text(_markdown(cfg, c, cal, pngs, salida), encoding="utf-8")
    viejo = salida / "resumen.md"
    if viejo.exists():
        viejo.unlink()
    return {"excel": excel, "reporte": md, "graficos": pngs, "powerfactory": exportados}


def _excel(ruta: Path, cfg: dict, c: dict, cal: dict, pngs: dict):
    res_val, metr, tec, rec_val = c["val"]
    datos = pd.DataFrame([
        ("Periodo", f"{cal['inicio']} a {cal['fin']}"),
        ("Días simulados", f"{cal['dias']} (todos los del periodo: {cal['laborables']} laborables, "
                           f"{cal['sabados']} sábados, {cal['domingos']} domingos)"),
        ("Horas", cal["horas"]),
        ("Resolución", "horaria; hora 1 = 00:00-01:00 (convención XM)"),
        ("Datos descargados", f"{dt.date.today()} (XM puede revisar cifras en versiones posteriores)"),
    ], columns=["concepto", "valor"])
    fuentes = pd.DataFrame(FUENTES, columns=["variable", "metrica", "fuente", "resolucion", "unidad", "uso"])
    with pd.ExcelWriter(ruta, engine="openpyxl") as xw:
        datos.to_excel(xw, sheet_name="1_Datos", index=False)
        fuentes.to_excel(xw, sheet_name="1_Datos", index=False, startrow=len(datos) + 2)
        c["perfil"].round(1).to_excel(xw, sheet_name="2_SIN_por_hora")
        c["diario"].round(1).to_excel(xw, sheet_name="2_SIN_por_dia")
        c["mezcla"].round(1).to_excel(xw, sheet_name="2_Real_vs_ideal")
        c["serie_real"].round(2).to_excel(xw, sheet_name="2_SIN_horario_744h")
        c["desglose"].round(1).to_excel(xw, sheet_name="3_Solar_por_clase", index=False)
        pd.Series(c["ind_sin"], name="valor").to_frame().to_excel(xw, sheet_name="3_4_Indicadores_SIN")
        pd.Series(metr, name="valor").to_frame().to_excel(xw, sheet_name="5_Validacion")
        tec.to_excel(xw, sheet_name="5_Validacion", startrow=len(metr) + 3)
        rec_val.to_excel(xw, sheet_name="5_Validacion", startrow=len(metr) + len(tec) + 7)
        c["tabla"].set_index("escenario").T.to_excel(xw, sheet_name="6_Escenarios")
        if c.get("barrido") is not None:
            c["barrido"].to_excel(xw, sheet_name="6_Barrido", index=False)
        if c.get("optimo") is not None:
            pd.Series(c["optimo"], name="valor").to_frame().to_excel(xw, sheet_name="7_Optimo")
        if c.get("sens") is not None:
            c["sens"].to_excel(xw, sheet_name="8_Sensibilidad_reserva", index=False)
        for nombre, (ideal, con_res) in c["resultados"].items():
            h = ideal.serie_horaria()
            if con_res.reserva:
                h = h.join(con_res.serie_horaria().filter(like="reserva_"))
            h.to_excel(xw, sheet_name=f"H_{nombre}"[:31])
        _supuestos(cfg).to_excel(xw, sheet_name="9_Supuestos", index=False)
        _insertar_graficos(xw, pngs)


def _supuestos(cfg: dict) -> pd.DataFrame:
    filas = []

    def rec(prefijo, x):
        if isinstance(x, dict):
            for k, v in x.items():
                rec(f"{prefijo}.{k}" if prefijo else k, v)
        elif not (isinstance(x, list) and x and isinstance(x[0], dict)):
            filas.append({"parametro": prefijo, "valor": str(x)})

    rec("", {k: v for k, v in cfg.items() if k != "escenarios"})
    return pd.DataFrame(filas)


def _insertar_graficos(xw, pngs: dict):
    from openpyxl.drawing.image import Image

    ws = xw.book.create_sheet("Graficos")
    fila = 1
    for ruta in pngs.values():
        img = Image(str(ruta))
        escala = 900 / img.width
        img.width, img.height = img.width * escala, img.height * escala
        ws.add_image(img, f"A{fila}")
        fila += int(img.height / 20) + 2


def _markdown(cfg: dict, c: dict, cal: dict, pngs: dict, salida: Path) -> str:
    ins, ind = c["ins"], c["ind_sin"]
    res_val, metr, tec, _ = c["val"]
    img = lambda k, alt: f"![{alt}]({Path(pngs[k]).relative_to(salida)})" if k in pngs else ""  # noqa: E731
    mz = c["mezcla"]
    L = []
    w = L.append

    w(f"# Despacho del SIN con y sin FNCER — {cal['inicio']:%d/%m/%Y} a {cal['fin']:%d/%m/%Y}\n")
    w("1. [Datos usados](#1-datos-usados) · 2. [Comportamiento real del SIN](#2-comportamiento-real-del-sin) · "
      "3. [Demanda y solar del mediodía](#3-demanda-y-solar-del-mediodía) · 4. [Inercia](#4-inercia-y-generación-no-sincrónica) · "
      "5. [Validación](#5-validación-del-modelo) · 6. [Escenarios](#6-escenarios) · 7. [Entrada óptima](#7-entrada-óptima-de-fncer) · "
      "8. [Sensibilidad de reserva](#8-sensibilidad-al-requerimiento-de-reserva) · 9. [Supuestos](#9-supuestos-y-limitaciones)\n")

    # 1 ------------------------------------------------------------------------------------------
    w("## 1. Datos usados\n")
    w(f"- **Días:** {cal['inicio']:%d/%m/%Y} a {cal['fin']:%d/%m/%Y}, **{cal['dias']} días** consecutivos "
      f"({cal['laborables']} laborables, {cal['sabados']} sábados y {cal['domingos']} domingos). No se excluyó ningún día.")
    w(f"- **Resolución horaria:** {cal['horas']} horas. La hora 1 va de 00:00 a 01:00 (convención de XM). "
      "Cuando un gráfico dice \"por hora del día\", es el promedio de esa hora en todos los días del periodo. "
      "Las fechas con hora (p. ej. 2026-10-15 18:00) marcan el **inicio** de la hora: 18:00 es la hora 19 de XM.")
    w("- **Cada día con sus propios datos:** ofertas, disponibilidad, inflexibilidades y solar de ese día. "
      "No hay un día típico: el modelo recorre las horas reales una por una.")
    w(f"- **Descarga:** {dt.date.today():%d/%m/%Y}. XM puede revisar cifras después; de SIMEM se usa la versión de liquidación más reciente.\n")
    w(_md_tabla(pd.DataFrame(FUENTES, columns=["Variable", "Métrica", "Fuente", "Resolución", "Unidad", "Uso"])))
    w("")

    # 2 ------------------------------------------------------------------------------------------
    w("## 2. Comportamiento real del SIN\n")
    w(f"Datos medidos por XM, sin modelo. Demanda total **{_f(ind['demanda_GWh'])} GWh**, media "
      f"**{_f(ind['demanda_media_MW'])} MW** y máxima **{_f(ind['demanda_max_MW'])} MW** "
      f"({ind['demanda_max_cuando'][:16]}). El pico diario promedio cae en la {_h(ind['hora_pico_demanda_promedio'])}. "
      f"Precio de bolsa medio: **{_f(ind['precio_bolsa_medio_COP_kWh'])} COP/kWh**.\n")
    w(img("sin_perfil", "Generación real por hora del día") + "\n")
    w("### 2.1 Operación real frente al despacho ideal\n")
    term = mz.loc["TERMICA"] if "TERMICA" in mz.index else None
    if term is not None:
        w(f"En la operación física, las térmicas aportaron **{_f(term['real_pct'])} %** de la energía, pero el despacho "
          f"ideal (sin red, el que fija el precio de bolsa) solo les asignaba **{_f(term['ideal_pct'])} %**. La diferencia "
          f"está en lo que XM registra para las térmicas como generación **fuera de mérito ({_f(term['fuera_merito_GWh'])} GWh)** "
          f"y **de seguridad ({_f(term['seguridad_GWh'])} GWh)**: energía que la operación real necesitó por restricciones "
          "de la red y del sistema, y que el despacho ideal no ve. El modelo reproduce el despacho ideal, así que su "
          "mezcla se compara con la columna *Ideal*, no con la *Real*.\n")
    mzt = mz.reset_index().rename(columns={"tipo": "Tipo", "real_GWh": "Real GWh", "ideal_GWh": "Ideal GWh",
                                           "seguridad_GWh": "Seguridad GWh", "fuera_merito_GWh": "Fuera de mérito GWh",
                                           "real_pct": "Real %", "ideal_pct": "Ideal %"})
    w(_md_tabla(mzt, 1) + "\n")
    w("### 2.2 Perfil promedio por hora\n")
    cols = {"demanda_MW": "Demanda", "Hidráulica_MW": "Hidráulica", "Térmica_MW": "Térmica",
            "Solar despachada_MW": "Solar desp.", "Solar no despachada_MW": "Solar no desp.", "Eólica_MW": "Eólica",
            "demanda_neta_MW": "Demanda neta", "precio_bolsa_COP_kWh": "Bolsa COP/kWh", "inercia_GWs": "Inercia GW·s"}
    p = c["perfil"][[k for k in cols if k in c["perfil"]]].rename(columns=cols).reset_index().rename(columns={"hora": "Hora"})
    w("Valores en MW (promedio de todos los días para cada hora).\n")
    w(_md_tabla(p, {"Hora": 0, **{v: 0 for v in cols.values()}, "Inercia GW·s": 1}) + "\n")
    w("### 2.3 Resumen por día\n")
    dcols = {"dia": "Día", "demanda_GWh": "Demanda GWh", "demanda_max_MW": "Dem. máx MW", "hora_demanda_max": "Hora máx",
             "solar_GWh": "Solar GWh", "solar_max_pct_demanda": "Solar máx %", "demanda_neta_min_MW": "Neta mín MW",
             "rampa_max_demanda_neta_MW_h": "Rampa máx MW/h", "precio_bolsa_medio_COP_kWh": "Bolsa media",
             "inercia_min_GWs": "Inercia mín GW·s"}
    d = c["diario"][[k for k in dcols if k in c["diario"]]].rename(columns=dcols)
    d.insert(0, "Fecha", [f"{x:%d/%m}" for x in d.index])
    w("La hora máxima usa la convención de XM (hora 19 = 18:00–19:00). Los fines de semana y el lunes festivo "
      "12/10 (Día de la Raza) tienen menos demanda, así que su participación solar es mayor y su inercia mínima, más baja.\n")
    w(_md_tabla(d, {"Demanda GWh": 1, "Dem. máx MW": 0, "Hora máx": 0, "Solar GWh": 1, "Solar máx %": 1,
                    "Neta mín MW": 0, "Rampa máx MW/h": 0, "Bolsa media": 0, "Inercia mín GW·s": 1}) + "\n")

    # 3 ------------------------------------------------------------------------------------------
    w("## 3. Demanda y solar del mediodía\n")
    w("### 3.1 Qué muestran los datos\n")
    h = ind["hora_pico_solar_promedio"]
    w(f"- La solar llega a su máximo en la **{_h(h)}**: en promedio **{_f(ind['solar_total_en_pico_MW'])} MW**, "
      f"el **{_f(ind['solar_en_pico_pct_demanda'])} %** de la demanda de esa hora ({_f(ind['demanda_en_pico_solar_MW'])} MW). "
      f"De eso, **{_f(ind['solar_no_despachada_en_pico_MW'])} MW** son solar que XM no despacha (plantas menores, GD, AGPE, autogeneradores).")
    w(f"- Con la solar descontada, la demanda neta del mediodía baja a **{_f(ind['demanda_neta_en_pico_solar_MW'])} MW**. "
      f"El mínimo del mes fue **{_f(ind['demanda_neta_min_MW'])} MW** ({ind['demanda_neta_min_cuando'][:16]}).")
    w(f"- La participación solar instantánea más alta fue **{_f(ind['solar_max_instantanea_pct'], 1)} %** "
      f"({ind['solar_max_instantanea_cuando'][:16]}).")
    w(f"- Al caer el sol, la demanda neta sube **{_f(ind['rampa_tarde_promedio_MW_h'])} MW/h** en promedio en la "
      f"{_h(ind['rampa_tarde_promedio_hora'])}. La rampa horaria más fuerte del mes fue de **{_f(ind['rampa_max_instantanea_MW_h'])} MW/h** "
      f"({ind['rampa_max_instantanea_cuando'][:16]}).")
    w(f"- Entre la hora {ind['subida_tarde_desde_hora']} y la {ind['subida_tarde_hasta_hora']} la demanda neta sube en promedio "
      f"**{_f(ind['subida_tarde_demanda_neta_MW'])} MW**: las hidráulicas aportan **{_f(ind['subida_tarde_hidraulica_MW'])} MW "
      f"({_f(ind['subida_tarde_hidraulica_pct'])} %)** y las térmicas {_f(ind['subida_tarde_termica_MW'])} MW. "
      "La flexibilidad de los embalses es lo que absorbe hoy el efecto de la solar.\n")
    w(img("demanda_neta", "Demanda y demanda neta") + "\n")
    w(_md_tabla(c["desglose"].rename(columns={"categoria": "Categoría", "energia_GWh": "Energía GWh", "pico_MW": "Pico MW",
                                              "media_12_13h_MW": "Media 12-13 h MW", "pct_demanda_12_13h": "% demanda 12-13 h"}),
                {"Energía GWh": 1, "Pico MW": 0, "Media 12-13 h MW": 0, "% demanda 12-13 h": 1}) + "\n")
    w("### 3.2 Cómo lo considera el modelo\n")
    w("1. **Demanda:** es `DemaCome`, medida en las fronteras comerciales, hora a hora y día a día. El autoconsumo de los "
      "autogeneradores detrás del medidor no aparece en ningún dato de XM: ya viene descontado de esa demanda.")
    w("2. **Solar no despachada** (plantas menores, GD, AGPE, autogeneradores): no oferta precio. Entra como inyección "
      "obligatoria igual a lo que generó cada hora real y se resta de la demanda. Así el valle del mediodía queda en "
      "el modelo tal como ocurrió, sin promedios ni día típico.")
    w("3. **Solar despachada** (las plantas grandes): oferta precio (~76 COP/kWh) y entra por orden de mérito con su "
      "disponibilidad horaria. Como es la oferta más barata, se despacha completa casi siempre.")
    w("4. **Lo que cubre el despacho central** es la demanda residual (línea aqua del gráfico): demanda menos todo lo "
      "que no oferta. Esa es la que se reparte entre hidráulicas, térmicas y solar despachada al menor costo.")
    w("5. **Escenarios con más solar:** en modo *distribuida*, la nueva solar se suma a la inyección obligatoria con el "
      "perfil horario real de la flota, lo que hunde más el valle. Si el sistema ya no puede bajar más (inflexibilidades), "
      "el excedente se vierte. En modo *despachada* la nueva solar oferta precio y se puede recortar.\n")

    # 4 ------------------------------------------------------------------------------------------
    w("## 4. Inercia y generación no sincrónica\n")
    w("**XM y SIMEM no publican la inercia del sistema** (se revisaron los catálogos completos). Se estima así: "
      "E = Σ H·S de las unidades sincrónicas en línea cada hora. El número de unidades sale del listado de SIMEM "
      "(670221) y la generación de cada planta; H es un valor típico por tecnología (hidráulica 3 s, térmica 4 s), "
      "editable en `config.yaml`. Se supone el mínimo de unidades necesario, así que **es una cota inferior**. "
      "Solar y eólica (inversores) aportan cero.\n")
    if "inercia_media_GWs" in ind:
        w(f"- Operación real de octubre: inercia media **{_f(ind['inercia_media_GWs'], 1)} GW·s**, mínima "
          f"**{_f(ind['inercia_min_GWs'], 1)} GW·s** ({ind['inercia_min_cuando'][:16]}), máxima {_f(ind['inercia_max_GWs'], 1)} GW·s.")
        w(f"- La inercia más baja ocurre en las horas de sol (en promedio, {_h(ind['hora_inercia_min_promedio'])}), cuando la "
          f"solar desplaza hidráulicas. La generación no sincrónica llegó a **{_f(ind['no_sincronica_max_pct'], 1)} %** de la demanda.")
        w(f"- Constante de inercia equivalente mínima del sistema: **{_f(ind['H_sistema_min_s'], 1)} s** sobre la demanda.")
        w("- La inercia del modelo sale del despacho ideal, que usa menos térmicas que la operación real "
          "(sección 2.1). Por eso queda **por debajo** de la real: compara los escenarios entre sí, no contra la línea gris.\n")
    w(img("inercia", "Inercia y generación no sincrónica") + "\n")
    t = c["tabla"]
    icol = {"escenario": "Escenario", "inercia_media_GWs": "Inercia media GW·s", "inercia_min_GWs": "Inercia mín GW·s",
            "inercia_min_fecha_hora": "Cuándo", "H_sistema_min_s": "H mín s", "no_sincronica_12h_media_pct": "No sincr. 12-13 h %",
            "no_sincronica_max_pct": "No sincr. máx %"}
    if "inercia_min_GWs" in t:
        ti = t[list(icol)].rename(columns=icol)
        ti["Cuándo"] = ti["Cuándo"].str[:16]
        w(_md_tabla(ti, 1) + "\n")

    # 5 ------------------------------------------------------------------------------------------
    w("## 5. Validación del modelo\n")
    w("El modelo se compara con el despacho ideal publicado por XM (mismo planteamiento: uninodal y sin AGC).\n")
    w(f"- Veredicto: **{metr['veredicto']}**")
    w(f"- Precio marginal: error medio **{_f(100 * metr['mape_precio'], 1)} %**, MAE {_f(metr['mae_precio'], 1)} COP/kWh, "
      f"sesgo {_f(metr['sesgo_precio'], 1)} COP/kWh, exacto en {_f(100 * metr['horas_precio_exacto_pct'])} % de las horas.")
    w(f"- Precio de bolsa medio: modelo {_f(metr['bolsa_media_modelo'], 1)} frente a XM {_f(metr['bolsa_media_xm'], 1)} COP/kWh.")
    w(f"- Energía diaria por planta: error ponderado **{_f(100 * metr['wape_energia_diaria'], 1)} %**. "
      f"Recurso marginal igual al de XM en {_f(100 * metr['recurso_marginal_coincide_pct'])} % de las horas.")
    w("- El error se concentra en las puntas de 18 a 20 h: el LP no tiene arranques ni mínimos técnicos de las térmicas.\n")
    w(img("val_precio", "Validación de precio") + "\n")
    w(img("val_recursos", "Validación por planta") + "\n")
    tt = tec.reset_index().rename(columns={"index": "Tecnología", "modelo_GWh": "Modelo GWh", "xm_GeneIdea_GWh": "XM ideal GWh",
                                           "diferencia_GWh": "Diferencia GWh", "diferencia_pct": "Diferencia %"})
    tt["Diferencia %"] *= 100
    w(_md_tabla(tt, 1) + "\n")

    # 6 ------------------------------------------------------------------------------------------
    w("## 6. Escenarios\n")
    w("Cada escenario usa los mismos días y horas del periodo. El precio sale del despacho ideal y la reserva de una "
      "segunda corrida con AGC co-optimizado.\n")
    ecol = {"escenario": "Escenario", "solar_nueva_MW": "Solar nueva MW", "eolica_nueva_MW": "Eólica nueva MW",
            "precio_bolsa_ponderado_COP_kWh": "Bolsa COP/kWh", "delta_precio_pct": "Δ precio %",
            "precio_bolsa_pico_18_21h_COP_kWh": "Bolsa 18-21 h", "participacion_fncer_pct": "FNCER %",
            "emisiones_ktCO2e": "ktCO2e", "delta_emisiones_pct": "Δ emis. %", "vertimiento_fncer_nueva_pct": "Vertim. %",
            "reserva_req_media_MW": "Reserva MW", "reserva_costo_milmill_COP": "Costo reserva mil M COP"}
    w(_md_tabla(t[[k for k in ecol if k in t]].rename(columns=ecol),
                {"Solar nueva MW": 0, "Eólica nueva MW": 0, "Bolsa COP/kWh": 0, "Bolsa 18-21 h": 0, "ktCO2e": 0,
                 "Reserva MW": 0, "Δ precio %": 1, "FNCER %": 1, "Δ emis. %": 1, "Vertim. %": 1,
                 "Costo reserva mil M COP": 1}) + "\n")
    for k, alt in (("precio_hora", "Precio por hora"), ("impacto", "Impacto"), ("mezcla", "Mezcla"), ("barrido", "Barrido")):
        if k in pngs:
            w(img(k, alt) + "\n")

    # 7-8 ----------------------------------------------------------------------------------------
    if c.get("optimo") is not None:
        o = c["optimo"]
        w("## 7. Entrada óptima de FNCER\n")
        w(f"Solar **{_f(o['solar_optimo_MW'])} MW** (límite {_f(float(o['solar_limite_MW']))}), eólica "
          f"**{_f(o['eolica_optimo_MW'])} MW** (límite {_f(float(o['eolica_limite_MW']))}). LCOE implícito: solar "
          f"{_f(o['solar_lcoe_implicito_COP_kWh'])} COP/kWh y eólica {_f(o['eolica_lcoe_implicito_COP_kWh'])} COP/kWh. "
          "Si una tecnología queda en su límite, el resultado lo fija el límite y no el costo: revisa los supuestos.\n")
    if c.get("sens") is not None:
        w("## 8. Sensibilidad al requerimiento de reserva\n")
        s = c["sens"].drop(columns=["escenario"])
        w(f"Escenario: `{c['sens']['escenario'].iloc[0]}`. Factor = MW de AGC exigidos por MW de FNCER nueva generado.\n")
        w(_md_tabla(s, 2) + "\n")

    # 9 ------------------------------------------------------------------------------------------
    w("## 9. Supuestos y limitaciones\n")
    w("- Red uninodal, como el despacho ideal: sin transmisión, sin generación de seguridad ni fuera de mérito (sección 2.1).")
    w("- LP: sin arranques, mínimos técnicos ni rampas térmicas (subestima algunas puntas).")
    w("- Hidráulicas a su precio de oferta diario; el agua desplazada no se traslada a otros días.")
    w("- Perfil eólico de solo dos parques en pruebas (Guajira I y WESP01): factor de planta probablemente alto.")
    w("- Factores de reserva, costos de inversión y constantes H son supuestos editables (`config.yaml`, hoja `9_Supuestos`).\n")
    w("## Archivos\n")
    w("- `resultados_despacho.xlsx`: hojas numeradas igual que estas secciones; `H_<escenario>` con las 744 horas.")
    w("- `powerfactory/generacion_real_XM.csv`: generación real por planta (MW/hora), el caso más representativo para estudios de red.")
    w("- `powerfactory/despacho_modelo_<escenario>.csv`: despacho del modelo por planta con oferta + FNCER nueva.")
    return "\n".join(L) + "\n"

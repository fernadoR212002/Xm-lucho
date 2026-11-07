"""Extracción de datos de XM con caché local en Parquet.

Fuentes:
  * API XM  (pydataxm.ReadDB)     -> ofertas, disponibilidad, generación, demanda, precios.
  * SIMEM   (pydatasimem.ReadSIMEM) -> holgura AGC (reserva) e inflexibilidades, que la API XM
                                     no publica en MW.

Todas las series se devuelven en formato largo:
  horario: fecha (día), hora (1..24, convención XM: hora 1 = 00:00-01:00), codigo, valor
  diario:  fecha, codigo, valor

La caché se guarda por mes calendario. El nombre del archivo registra hasta qué fecha se pidió
(`2026-10__hasta-2026-10-31.parquet`), así un mes que estaba incompleto se vuelve a descargar
cuando se pide una fecha posterior.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import logging
from pathlib import Path

import pandas as pd

HORAS_XM = [f"Values_Hour{h:02d}" for h in range(1, 25)]
COLS_HORARIO = ["fecha", "hora", "codigo", "valor"]
COLS_DIARIO = ["fecha", "codigo", "valor"]

log = logging.getLogger(__name__)


def _a_fecha(x) -> dt.date:
    return pd.Timestamp(x).date()


def _meses(ini: dt.date, fin: dt.date):
    """Itera (inicio_mes, fin_mes, fin_pedido_en_ese_mes) cubriendo [ini, fin]."""
    actual = ini.replace(day=1)
    while actual <= fin:
        fin_mes = (pd.Timestamp(actual) + pd.offsets.MonthEnd(0)).date()
        yield actual, fin_mes, min(fin_mes, fin)
        actual = fin_mes + dt.timedelta(days=1)


class _CacheMensual:
    """Caché Parquet por (fuente, serie, mes)."""

    def __init__(self, raiz: Path, fuente: str, serie: str):
        self.dir = Path(raiz) / fuente / serie
        self.dir.mkdir(parents=True, exist_ok=True)

    def leer(self, mes: dt.date, hasta: dt.date) -> pd.DataFrame | None:
        for f in sorted(self.dir.glob(f"{mes:%Y-%m}__hasta-*.parquet")):
            hasta_cache = dt.date.fromisoformat(f.stem.split("__hasta-")[1])
            if hasta_cache >= hasta:
                return pd.read_parquet(f)
        return None

    def guardar(self, mes: dt.date, hasta: dt.date, df: pd.DataFrame) -> None:
        for f in self.dir.glob(f"{mes:%Y-%m}__hasta-*.parquet"):
            f.unlink()
        df.to_parquet(self.dir / f"{mes:%Y-%m}__hasta-{hasta.isoformat()}.parquet", index=False)


def _limite_publicacion(fin: dt.date) -> dt.date:
    """XM publica con rezago; nunca se pide más allá de ayer."""
    return min(fin, dt.date.today() - dt.timedelta(days=1))


# --------------------------------------------------------------------------------------------
# API XM
# --------------------------------------------------------------------------------------------
class FuenteXM:
    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)
        self._api = None

    @property
    def api(self):
        if self._api is None:
            from pydataxm.pydataxm import ReadDB

            self._api = ReadDB()
        return self._api

    @staticmethod
    def _normalizar(df: pd.DataFrame) -> pd.DataFrame:
        """Convierte la respuesta de pydataxm a formato largo con valores numéricos."""
        if df is None or df.empty:
            return pd.DataFrame(columns=COLS_HORARIO)
        if "Values_Hour01" in df.columns:
            horas = [h for h in HORAS_XM if h in df.columns]
            largo = df.melt(id_vars=["Values_code", "Date"], value_vars=horas,
                            var_name="hora", value_name="valor")
            largo["hora"] = largo["hora"].str[-2:].astype(int)
            largo = largo.rename(columns={"Values_code": "codigo", "Date": "fecha"})
        elif "Value" in df.columns:  # métricas diarias: Id, Code, Value, Date
            largo = df.rename(columns={"Code": "codigo", "Value": "valor", "Date": "fecha"})
            largo = largo[["fecha", "codigo", "valor"]]
        else:
            raise ValueError(f"Formato de respuesta no reconocido: {df.columns.tolist()}")
        largo["fecha"] = pd.to_datetime(largo["fecha"]).dt.normalize()
        largo["codigo"] = largo["codigo"].astype(str)
        largo["valor"] = pd.to_numeric(largo["valor"], errors="coerce")
        return largo.dropna(subset=["valor"]).reset_index(drop=True)

    def serie(self, metrica: str, entidad: str, ini, fin) -> pd.DataFrame:
        """Serie horaria o diaria de una métrica XM entre ini y fin (inclusive)."""
        ini, fin = _a_fecha(ini), _limite_publicacion(_a_fecha(fin))
        cache = _CacheMensual(self.cache_dir, "xm", f"{metrica}_{entidad}")
        partes = []
        for mes, fin_mes, hasta in _meses(ini, fin):
            df = cache.leer(mes, hasta)
            if df is None:
                hasta_descarga = _limite_publicacion(fin_mes)
                log.info("Descargando XM %s/%s %s..%s", metrica, entidad, mes, hasta_descarga)
                df = self._normalizar(self.api.request_data(metrica, entidad, mes, hasta_descarga))
                cache.guardar(mes, hasta_descarga, df)
            partes.append(df)
        df = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLS_HORARIO)
        if df.empty:
            return df
        f = df["fecha"]
        return df[(f >= pd.Timestamp(ini)) & (f <= pd.Timestamp(fin))].reset_index(drop=True)

    def listado_recursos(self, refrescar: bool = False) -> pd.DataFrame:
        """Atributos de los recursos de generación (tipo, fuente, despacho central, clase)."""
        archivo = self.cache_dir / "xm" / "ListadoRecursos.parquet"
        if archivo.exists() and not refrescar:
            return pd.read_parquet(archivo)
        hoy = dt.date.today()
        df = self.api.request_data("ListadoRecursos", "Sistema", hoy, hoy)
        df = df.rename(columns={
            "Values_Code": "codigo", "Values_Name": "nombre", "Values_Type": "tipo",
            "Values_Disp": "despacho", "Values_RecType": "clase", "Values_EnerSource": "fuente",
            "Values_State": "estado", "Values_OperStartdate": "inicio_operacion",
        })
        df = df[["codigo", "nombre", "tipo", "despacho", "clase", "fuente", "estado", "inicio_operacion"]]
        for c in ["tipo", "despacho", "clase", "fuente", "estado"]:
            df[c] = df[c].astype(str).str.upper().str.strip()
        # Hay códigos repetidos: se prefiere el registro en operación.
        df["_orden"] = (df["estado"] != "OPERACION").astype(int)
        df = df.sort_values(["codigo", "_orden"]).drop_duplicates("codigo").drop(columns="_orden")
        archivo.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(archivo, index=False)
        return df


# --------------------------------------------------------------------------------------------
# SIMEM
# --------------------------------------------------------------------------------------------
class FuenteSIMEM:
    """Datasets horarios de SIMEM con columnas FechaHora / CodigoPlanta / Version / Valor."""

    def __init__(self, cache_dir: str | Path, preferencia_version=("TXF", "TXR", "TX2", "TX1")):
        self.cache_dir = Path(cache_dir)
        self.preferencia = list(preferencia_version)

    def _descargar(self, dataset: str, ini: dt.date, fin: dt.date) -> pd.DataFrame:
        from pydataxm.pydatasimem import ReadSIMEM

        logging.getLogger().disabled = True  # la librería usa el logger raíz de forma ruidosa
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return ReadSIMEM(dataset, ini.isoformat(), fin.isoformat()).main(filter=False)
        finally:
            logging.getLogger().disabled = False

    def _normalizar(self, df: pd.DataFrame, variable: str | None, codigo_col: str,
                    valor_col: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=COLS_HORARIO)
        if variable is not None and "CodigoVariable" in df.columns:
            df = df[df["CodigoVariable"] == variable]
        fh = pd.to_datetime(df["FechaHora"])
        out = pd.DataFrame({
            "fecha": fh.dt.normalize(),
            "hora": fh.dt.hour + 1,  # SIMEM 00:00 == hora 1 de XM (verificado contra GeneIdea)
            "codigo": df[codigo_col].astype(str),
            "valor": pd.to_numeric(df[valor_col], errors="coerce"),
            "version": df["Version"].astype(str) if "Version" in df.columns else "UNICA",
        })
        # Se conserva la versión más reciente de la liquidación disponible para cada dato.
        rango = {v: i for i, v in enumerate(self.preferencia)}
        out["_p"] = out["version"].map(rango).fillna(len(rango))
        out = (out.sort_values("_p")
                  .drop_duplicates(["fecha", "hora", "codigo"])
                  .drop(columns=["_p", "version"]))
        return out.dropna(subset=["valor"]).reset_index(drop=True)

    def tabla(self, dataset: str, ini, fin) -> pd.DataFrame:
        """Dataset SIMEM sin normalizar (por ejemplo, listados diarios), con caché mensual."""
        ini, fin = _a_fecha(ini), _limite_publicacion(_a_fecha(fin))
        cache = _CacheMensual(self.cache_dir, "simem", dataset)
        partes = []
        for mes, fin_mes, hasta in _meses(ini, fin):
            df = cache.leer(mes, hasta)
            if df is None:
                hasta_descarga = _limite_publicacion(fin_mes)
                log.info("Descargando SIMEM %s %s..%s", dataset, mes, hasta_descarga)
                df = self._descargar(dataset, mes, hasta_descarga).astype(str)
                cache.guardar(mes, hasta_descarga, df)
            partes.append(df)
        return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()

    def serie(self, dataset: str, ini, fin, variable: str | None = None,
              codigo_col: str = "CodigoPlanta", valor_col: str = "Valor") -> pd.DataFrame:
        ini, fin = _a_fecha(ini), _limite_publicacion(_a_fecha(fin))
        nombre = dataset if variable is None else f"{dataset}_{variable}"
        cache = _CacheMensual(self.cache_dir, "simem", nombre)
        partes = []
        for mes, fin_mes, hasta in _meses(ini, fin):
            df = cache.leer(mes, hasta)
            if df is None:
                hasta_descarga = _limite_publicacion(fin_mes)
                log.info("Descargando SIMEM %s %s..%s", dataset, mes, hasta_descarga)
                crudo = self._descargar(dataset, mes, hasta_descarga)
                df = self._normalizar(crudo, variable, codigo_col, valor_col)
                cache.guardar(mes, hasta_descarga, df)
            partes.append(df)
        df = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLS_HORARIO)
        if df.empty:
            return df
        f = df["fecha"]
        return df[(f >= pd.Timestamp(ini)) & (f <= pd.Timestamp(fin))].reset_index(drop=True)

"""Pruebas del LP de despacho en un sistema de juguete con solución conocida a mano.

Sistema: A (100 MW, 50 COP/kWh, presta AGC), B (100 MW, 80), C (100 MW, 200); demanda 150 MW.
"""
import numpy as np
import pandas as pd
import pytest

from despacho.modelo import Escenario, Parametros, despachar
from despacho.preparacion import Insumos

H = 24


def insumos(demanda=150.0, inflex_c=0.0, req=0.0, cf_solar=1.0, tipos=("HIDRAULICA", "HIDRAULICA", "TERMICA")):
    tiempo = pd.date_range("2026-01-01", periods=H, freq="h")
    rec = pd.DataFrame({"nombre": ["A", "B", "C"], "tipo": list(tipos), "agc": [True, False, False],
                        "factor_co2": [0.0, 0.0, 0.9]}, index=pd.Index(["A", "B", "C"], name="codigo"))
    costo = np.repeat([[50.0], [80.0], [200.0]], H, axis=1)
    disp = np.full((3, H), 100.0)
    inflex = np.zeros((3, H))
    inflex[2] = inflex_c
    return Insumos(
        tiempo=tiempo, recursos=rec, costo=costo, disp=disp, inflex=inflex,
        demanda=np.full(H, demanda), fijo=pd.DataFrame({"OTRO": np.zeros(H)}, index=tiempo),
        fijo_co2=np.zeros(H), gen_ideal=np.zeros((3, H)), precio_marginal_xm=np.full(H, 80.0),
        precio_bolsa_xm=np.full(H, 90.0), delta_i=np.full(H, 10.0), marginal_xm=np.full(H, "B", dtype=object),
        req_agc=np.full(H, req), cf={"solar": np.full(H, cf_solar), "eolica": np.zeros(H)},
        fncer_existente={"solar": np.zeros(H), "eolica": np.zeros(H)}, precio_fncer_ref=0.0)


def par(**kw):
    return Parametros(precio_oferta_fncer={"solar": 0.0, "eolica": 0.0}, **kw)


def test_orden_de_merito_y_precio_marginal():
    r = despachar(insumos(), Escenario("t"), par())
    np.testing.assert_allclose(r.g[:, 0], [100, 50, 0], atol=1e-6)
    np.testing.assert_allclose(r.precio, 80.0)
    np.testing.assert_allclose(r.cmg, 80.0, atol=1e-6)
    np.testing.assert_allclose(r.precio_bolsa, 90.0)
    assert (r.marginal == "B").all()


def test_inflexible_no_fija_precio():
    r = despachar(insumos(inflex_c=30), Escenario("t"), par())
    np.testing.assert_allclose(r.g[:, 0], [100, 20, 30], atol=1e-6)
    np.testing.assert_allclose(r.precio, 80.0)  # C genera pero es inflexible: no marca precio


def test_fncer_desplaza_y_baja_precio():
    r = despachar(insumos(), Escenario("t", solar_mw=100), par())
    np.testing.assert_allclose(r.y["solar"], 100.0, atol=1e-6)
    np.testing.assert_allclose(r.g[:, 0], [50, 0, 0], atol=1e-6)
    np.testing.assert_allclose(r.precio, 50.0)


def test_reserva_costo_de_oportunidad():
    # A presta AGC: para dejar 30 MW libres baja a 70 y B sube a 80.
    # Precio de la reserva = 80 - 50 = 30 COP/kWh (lo que cuesta mover energía de A a B).
    r = despachar(insumos(req=30), Escenario("t", reserva=True), par())
    np.testing.assert_allclose(r.g[:, 0], [70, 80, 0], atol=1e-6)
    # +0.01 = costo de desempate por MW reservado (Parametros.costo_reserva)
    np.testing.assert_allclose(r.reserva["precio_subir"], 30.0 + 0.01, atol=1e-6)
    np.testing.assert_allclose(r.reserva["faltante_subir"], 0.0, atol=1e-6)


def test_reserva_crece_con_fncer():
    p = par(factor_reserva={"solar": 0.10, "eolica": 0.0})
    r = despachar(insumos(req=10), Escenario("t", solar_mw=100, reserva=True), p)
    np.testing.assert_allclose(r.reserva["requerimiento"], 10 + 0.10 * 100)


@pytest.mark.parametrize("costo_anual, esperado", [(1.0, 100.0), (1e12, 0.0)])
def test_optimizacion_de_capacidad(costo_anual, esperado):
    # Barata: instala hasta el límite (100 MW); carísima: no instala nada.
    p = par(costo_anual_mw={"solar": costo_anual, "eolica": 0.0})
    esc = Escenario("t", optimizar_capacidad=True, solar_max_mw=100)
    r = despachar(insumos(), esc, p)
    assert r.capacidad["solar"] == pytest.approx(esperado, abs=1e-3)


def test_distribuida_vierte_excedente():
    # 200 MW de solar obligatoria con 150 MW de demanda: sobran 50 MW que se vierten.
    r = despachar(insumos(), Escenario("t", solar_mw=200, modo="distribuida"), par())
    np.testing.assert_allclose(r.vertimiento, 50.0, atol=1e-6)
    np.testing.assert_allclose(r.g.sum(axis=0), 0.0, atol=1e-6)


def test_retirar_existentes_saca_solar_ofertada():
    ins = insumos(tipos=("SOLAR", "HIDRAULICA", "TERMICA"))
    r = despachar(ins, Escenario("t", retirar_existentes=True), par())
    np.testing.assert_allclose(r.g[:, 0], [0, 100, 50], atol=1e-6)
    np.testing.assert_allclose(r.precio, 200.0)


def test_racionamiento_si_falta_oferta():
    r = despachar(insumos(demanda=350), Escenario("t"), par(costo_racionamiento=5000))
    np.testing.assert_allclose(r.deficit, 50.0, atol=1e-6)
    np.testing.assert_allclose(r.precio, 5000.0)

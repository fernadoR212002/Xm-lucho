"""Inercia: unidades en línea = ⌈g / tamaño de unidad⌉; E = H · S."""
import numpy as np

from despacho.inercia import energia_cinetica


def test_unidades_minimas_en_linea():
    # Planta hidráulica de 300 MW con 3 unidades de 100 MW, H = 3 s, factor de potencia 1.
    G = np.array([[0.0, 1.0, 150.0, 300.0]])
    E = energia_cinetica(G, H=[3.0], cap_mw=[300.0], n_unidades=[3], factor_potencia=1.0)
    # 0 MW -> 0 unidades; 1 MW -> 1; 150 MW -> 2; 300 MW -> 3. Cada unidad: 3 s · 100 MVA = 0,3 GW·s
    np.testing.assert_allclose(E, [0.0, 0.3, 0.6, 0.9])


def test_inversores_no_aportan():
    G = np.array([[500.0, 500.0], [100.0, 0.0]])  # fila 0 solar, fila 1 térmica de una unidad
    E = energia_cinetica(G, H=[0.0, 4.0], cap_mw=[600.0, 100.0], n_unidades=[1, 1], factor_potencia=0.9)
    np.testing.assert_allclose(E, [4.0 * 100 / 0.9 / 1000, 0.0])

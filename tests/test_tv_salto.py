"""El avance y el retroceso de la app de televisor, con un televisor simulado.

La lógica vive en JavaScript porque corre dentro del televisor, así que la prueba la
ejecuta Node sobre un DOM mínimo. Se comprueba lo que no se puede comprobar desde aquí
a mano: que un salto que el televisor ignora en silencio acabe pidiéndole la lista al
equipo, en vez de dejar el mando como si estuviera muerto.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRUEBA = ROOT / 'tests' / 'prueba-salto.js'


@pytest.mark.skipif(shutil.which('node') is None, reason='Node no está instalado en este equipo')
def test_avance_y_retroceso_en_el_televisor():
    hecho = subprocess.run(['node', str(PRUEBA)], cwd=ROOT, capture_output=True, text=True)
    assert hecho.returncode == 0, hecho.stdout + hecho.stderr
    assert 'FALLA' not in hecho.stdout, hecho.stdout

"""Enrutamiento de tareas a modelos (Model Router).

* ``router`` — tareas, candidatos, estrategias, política de privacidad y
  coste, y decisión con alternativas explicadas.
* ``executor`` — ejecuta una decisión de chat en streaming probando las
  alternativas permitidas y avisando de cada cambio.

Dominio puro: no importa Qt ni clientes HTTP; los candidatos los construye
la capa de infraestructura (``app/routing/candidates.py``).
"""

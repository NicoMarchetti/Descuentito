"""
Entry point para Vercel (Python serverless). Vercel busca un objeto WSGI
llamado `app` en cualquier archivo .py bajo /api. En vez de duplicar todo
el backend acá, reusamos directamente app.py (la misma fuente que corre
local con `python app.py` o en tu propio server con gunicorn).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app  # noqa: E402,F401 -- Vercel necesita este nombre exacto

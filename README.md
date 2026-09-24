# DESCUENTITO

Comparador de precios y ofertas de videojuegos en Argentina: **Steam**, **Nintendo eShop AR**, **Epic Games Store** y **GOG**, en un solo lugar.

🔗 [descuentito-seven.vercel.app](https://descuentito-seven.vercel.app/)

## Qué hace

- Busca un juego y compara su precio en las 4 tiendas al mismo tiempo (el de Nintendo AR en pesos reales, no una conversión)
- Feed de inicio con las ofertas destacadas del momento, sin buscar nada
- Conversor USD → ARS con los distintos tipos de dólar (oficial, blue, tarjeta, MEP, CCL)
- Watchlist personal (vive en el navegador, no hay cuentas ni backend con estado) para chequear si algo que te interesa entró en oferta

## Cómo está armado

```
app.py              # backend Flask (una sola API, sin base de datos)
requirements.txt
frontend/            # React + TypeScript + Vite + Tailwind/DaisyUI
```

El backend no tiene base de datos: todo sale en vivo de fuentes externas, con caché en memoria de 5 minutos.

| Tienda | Fuente | Notas |
|---|---|---|
| Steam | API pública oficial de Steam | Precio real en ARS |
| Nintendo eShop AR | Scraping de [DekuDeals](https://www.dekudeals.com/) | No hay API pública oficial; ver advertencia abajo |
| Epic Games Store / GOG | [CheapShark](https://www.cheapshark.com/api) | Precio en USD (esas tiendas no tienen precio local en AR) |
| Cotización del dólar | [dolarapi.com](https://dolarapi.com/) | |

### ⚠️ Sobre el scraping de DekuDeals

No existe una API pública oficial para consultar precios de Nintendo eShop Argentina. Este proyecto resuelve eso parseando el HTML de DekuDeals (que sí muestra precios en ARS). Esto significa:

- Si DekuDeals cambia la estructura de su sitio, esa parte se rompe hasta que se actualice el parser
- Epic/GOG (vía CheapShark) le devuelven **400 Bad Request** a las IPs de datacenter de Vercel — por eso el backend está pensado para correr en un servidor con IP residencial, no en el mismo Vercel del frontend (ver más abajo)
- Nunca lo trates como una fuente 100% estable — está armado para degradarse bien (si una fuente falla, esa parte queda vacía en vez de romper toda la página), pero no es una API con garantías

## Correr en local

### Backend

```bash
python3 -m venv venv
source venv/bin/activate       # en Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py                  # levanta en http://localhost:5000
```

### Frontend

```bash
cd frontend
npm install
npm run dev                    # levanta en http://localhost:5173, con proxy a :5000
```

## Deploy

- **Frontend**: Vercel (`frontend/` como Root Directory). Variable de entorno `VITE_API_BASE` apuntando a donde corra el backend.
- **Backend**: en un servidor propio (no en el mismo Vercel — ver la advertencia de arriba sobre CheapShark bloqueando IPs de datacenter), con `gunicorn` + `nginx` + certificado TLS (Let's Encrypt). Hay un `.service` de systemd de ejemplo pensado para esto.

## Licencia

Proyecto personal, sin licencia formal todavía. Si lo clonás o reusás algo, una mención está bien.

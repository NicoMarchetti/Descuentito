# DESCUENTITO

Comparador de precios y ofertas de videojuegos en Argentina: **Steam**, **Nintendo eShop AR**, **Epic Games Store**, **GOG**, **Xbox** y **PlayStation**, en un solo lugar.

🔗 [descuentito-seven.vercel.app](https://descuentito-seven.vercel.app/)

## Qué hace

- Feed de inicio con ofertas de las seis tiendas, sin buscar nada — con scroll infinito y filtros combinables por tienda, % de descuento mínimo y rango de precio
- Busca un juego puntual y lo compara en todas las tiendas donde aparece (el de Nintendo AR en pesos reales, no una conversión)
- Conversor USD → ARS con los distintos tipos de dólar (oficial, blue, tarjeta, MEP, CCL)
- Watchlist personal: se guarda en el navegador (no hay cuentas), pero chequear si algo que seguís entró en oferta sí le pega al backend en vivo contra las seis fuentes

## Cómo está armado

```
app.py              # backend Flask (una sola API, sin base de datos)
requirements.txt
frontend/            # React + TypeScript + Vite + Tailwind/DaisyUI
```

El backend no tiene base de datos: todo sale en vivo de fuentes externas. Nada se pide en vivo por cada visita — Steam, Epic/GOG, Xbox y PlayStation se traen enteros en jobs de fondo (cada varias horas o una vez al día, según la fuente) y quedan cacheados en memoria; así ningún volumen de tráfico al sitio multiplica los pedidos salientes a esas APIs.

| Tienda | Fuente | Notas |
|---|---|---|
| Steam | API pública oficial de Steam | Precio real en ARS |
| Nintendo eShop AR | Scraping de [DekuDeals](https://www.dekudeals.com/) | No hay API pública oficial; ver advertencia abajo |
| Epic Games Store / GOG | [CheapShark](https://www.cheapshark.com/api) | Precio en USD (esas tiendas no tienen precio local en AR) |
| Xbox | Canal interno de ofertas de xbox.com (sin documentar) | Se pagina entera una vez al día |
| PlayStation | GraphQL interno de store.playstation.com (sin documentar, Automatic Persisted Queries) | Precio en USD, igual que Epic/GOG |
| Cotización del dólar | [dolarapi.com](https://dolarapi.com/) | |

### ⚠️ Sobre las fuentes sin API pública (DekuDeals, Xbox, PlayStation)

Ni Nintendo eShop AR, ni Xbox, ni PlayStation tienen una API pública oficial para consultar precios. Esto se resuelve parseando HTML (DekuDeals) o replicando los pedidos internos que usan sus propios sitios web (Xbox, PlayStation) — endpoints no documentados, no pensados para consumo externo. Esto significa:

- Si cualquiera de los tres cambia la estructura de su sitio o de su API interna, esa parte se rompe hasta que se actualice
- Epic/GOG (vía CheapShark) y Steam le devuelven error / bloquean temporalmente a cualquier IP que pida demasiado seguido — por eso TODO lo que pagina un catálogo entero (Steam global, Xbox, PlayStation, precios de Steam para Epic/GOG) lo hace secuencial, con pausas entre pedido y pedido, nunca en paralelo, y cacheado en un job de fondo aparte
- El backend está pensado para correr en un servidor con IP residencial, no en el mismo Vercel del frontend (ver más abajo) — varias de estas fuentes bloquean o degradan pedidos desde IPs de datacenter
- Nunca las trates como una fuente 100% estable — está armado para degradarse bien (si una fuente falla, esa parte queda vacía en vez de romper toda la página), pero no son APIs con garantías
- Hay endpoints de diagnóstico para cada scraper (`/api/debug/xbox-deals`, `/api/debug/playstation-deals`) que muestran cuántas ofertas trajo la última corrida, si hubo errores, y hace cuánto se actualizó

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
- **Backend**: en un servidor propio (no en el mismo Vercel — ver la advertencia de arriba sobre IPs de datacenter), con `gunicorn` + `nginx` + certificado TLS (Let's Encrypt). Hay un `.service` de systemd de ejemplo pensado para esto.

## Licencia

Proyecto personal, sin licencia formal todavía. Si lo clonás o reusás algo, una mención está bien.

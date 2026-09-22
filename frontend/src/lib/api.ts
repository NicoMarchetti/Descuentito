import type { CompareResponse, DolarRate, WatchlistDeal, WatchlistItem } from "./types";

// En dev, Vite proxea /api -> http://localhost:5000 (ver vite.config.ts).
// En producción, apuntá esto a tu backend con VITE_API_BASE en el .env.
const BASE = import.meta.env.VITE_API_BASE ?? "";

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error ?? `Error ${r.status} en ${path}`);
  }
  return r.json();
}

export function compare(query: string): Promise<CompareResponse> {
  return getJSON(`/api/compare?q=${encodeURIComponent(query)}`);
}

export function home(page = 1): Promise<CompareResponse> {
  return getJSON(`/api/home?page=${page}`);
}

export function getDolarRates(): Promise<DolarRate[]> {
  return getJSON(`/api/dolar`);
}

// La watchlist en sí vive en localStorage (ver lib/watchlist.ts) -- esto
// solo le manda al backend la lista actual para chequear precios/ofertas,
// sin que el server guarde nada.
export async function checkWatchlistDeals(
  items: WatchlistItem[],
): Promise<WatchlistDeal[]> {
  const r = await fetch(`${BASE}/api/watchlist/deals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error ?? "No se pudieron chequear las ofertas");
  }
  return r.json();
}

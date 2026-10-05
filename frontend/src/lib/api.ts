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

export type HomeSort = "descuento" | "precio" | "relevancia" | "mas_vendidas";

// Filtros del sidebar -- todos opcionales, ver app.py (route_home) para
// el detalle de cómo se aplican. "stores" acá solo puede traer
// "steam"/"epic"/"gog" (Switch no se puede filtrar desde /api/home, ver
// el comentario en _filter_candidates del backend).
export interface HomeFilters {
  stores?: ("steam" | "epic" | "gog")[];
  minDiscount?: number;
  priceMin?: number;
  priceMax?: number;
}

export function home(
  page = 1,
  sort: HomeSort = "descuento",
  pageSize?: number,
  onlyDiscounted?: boolean,
  filters?: HomeFilters,
): Promise<CompareResponse> {
  const params = new URLSearchParams({ page: String(page), sort });
  if (pageSize) params.set("page_size", String(pageSize));
  if (onlyDiscounted) params.set("only_discounted", "1");
  if (filters?.stores?.length) params.set("stores", filters.stores.join(","));
  if (filters?.minDiscount) params.set("min_discount", String(filters.minDiscount));
  if (filters?.priceMin !== undefined) params.set("price_min", String(filters.priceMin));
  if (filters?.priceMax !== undefined) params.set("price_max", String(filters.priceMax));
  return getJSON(`/api/home?${params.toString()}`);
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

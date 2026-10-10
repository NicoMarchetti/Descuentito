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
// el detalle de cómo se aplican. "stores" acá puede traer
// "steam"/"epic"/"gog"/"xbox"/"playstation" (Switch no se puede filtrar
// desde /api/home, ver el comentario en _filter_candidates del backend).
export interface HomeFilters {
  stores?: ("steam" | "epic" | "gog" | "xbox" | "playstation")[];
  minDiscount?: number;
  priceMin?: number;
  priceMax?: number;
  // true cuando el filtro "Switch" del sidebar está activo -- ver el
  // comentario grande en App.tsx (switchOnly) sobre por qué Switch no se
  // puede filtrar como las demás tiendas. Esto NO le pide al backend que
  // filtre por Switch (sigue sin poder): solo le pide que descarte de
  // entrada los candidatos que NUNCA van a poder matchear (los que
  // llegaron SOLO por Xbox/PlayStation, sin steam_appid -- ver
  // _merge_xbox_into/_merge_ps_into) antes de paginar. Sin esto, con
  // Xbox+PlayStation sumando varios cientos de candidatos sin appid al
  // final del pool, el scroll infinito tenía que paginar a través de
  // TODOS esos (ninguno puede estar en Switch) para encontrar el
  // siguiente match real -- se veía como si el filtro de Switch se
  // quedara pegado en "cargando" para siempre.
  switchOnly?: boolean;
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
  if (filters?.switchOnly) params.set("switch_only", "1");
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

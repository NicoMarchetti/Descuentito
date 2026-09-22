export interface SteamPrice {
  available: boolean;
  is_free?: boolean;
  currency?: string;
  initial_price?: number;
  final_price?: number;
  discount_percent?: number;
  reason?: string;
}

export interface SteamResult {
  appid: number;
  name: string;
  tiny_image?: string;
  price?: SteamPrice;
}

export interface NintendoCheck {
  on_switch: boolean;
  nsuid?: string;
  currency?: string;
  price?: number;
  discount_percent?: number;
}

export interface MatchedEntry {
  name: string;
  steam: SteamResult;
  nintendo: NintendoCheck;
}

export interface PcStoreDeal {
  store: "Epic Games Store" | "GOG" | string;
  name: string;
  currency: string;
  initial_price: number;
  final_price: number;
  discount_percent: number;
  deal_url: string;
  thumb?: string;
}

export interface NintendoDirectResult {
  slug: string;
  name: string;
  image?: string;
  currency: string;
  available: boolean;
  price?: number;
}

export interface CompareResponse {
  query: string;
  matched: MatchedEntry[];
  steam_only: SteamResult[];
  pc_stores: PcStoreDeal[];
  // Solo viene en /api/compare (búsqueda manual): resultados encontrados
  // directo en Nintendo, exista o no el juego en Steam/Epic/GOG.
  nintendo_direct?: NintendoDirectResult[];
  // Solo vienen presentes en /api/home (paginado); /api/compare no los manda.
  page?: number;
  page_size?: number;
  total?: number;
  has_more?: boolean;
}

export interface DolarRate {
  moneda: string;
  casa: string;
  nombre: string;
  compra: number;
  venta: number;
  fechaActualizacion: string;
}

export interface WatchlistItem {
  id: string;
  kind: "steam" | "nintendo";
  appid?: number;
  slug?: string;
  name: string;
}

export interface WatchlistDeal {
  id: string;
  name: string;
  steam: SteamPrice | null;
  nintendo: NintendoCheck;
}

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
  // Solo viene en /api/home (paginado): posición real de este candidato
  // en la lista COMPLETA, no solo en esta página -- ver el comentario en
  // route_home (app.py) y en buildGameGroups (CompareTable.tsx) sobre por
  // qué hace falta para fusionar "matched"/"steam_only" en el orden
  // correcto. /api/compare no lo manda (no hace falta: no se pagina).
  order?: number;
}

export interface NintendoCheck {
  on_switch: boolean;
  nsuid?: string;
  currency?: string;
  price?: number;
  discount_percent?: number;
  // Heurística (ver app.py): busca "Switch 2" en el HTML de DekuDeals.
  // No está 100% verificado, puede dar falsos positivos.
  switch2?: boolean;
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
  // Identifica al juego en CheapShark (no a esta oferta puntual); con
  // esto se puede "seguir" un resultado de Epic/GOG igual que un appid
  // de Steam o un slug de DekuDeals.
  game_id?: string;
}

export interface NintendoDirectResult {
  slug: string;
  name: string;
  image?: string;
  currency: string;
  available: boolean;
  price?: number;
  switch2?: boolean;
}

// Conteos para el sidebar de filtros de /api/home -- "cuántas ofertas
// quedarían si además marcás esta tienda/este descuento mínimo", ya
// calculados sobre el pool completo (antes de paginar) con el resto de
// los filtros activos aplicados. Ver home_facets en app.py.
export interface HomeFacets {
  stores: { steam?: number; epic?: number; gog?: number; xbox?: number };
  discount: { "50"?: number; "70"?: number; "90"?: number };
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
  facets?: HomeFacets;
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
  kind: "steam" | "nintendo" | "pc";
  appid?: number;
  slug?: string;
  // Solo para kind "pc" (seguido directo en Epic/GOG/Xbox).
  store?: "epic" | "gog" | "xbox";
  gameId?: string;
  name: string;
  // Capturada al momento de tocar "+ seguir" (viene de la tarjeta de
  // origen), para poder mostrar la tarjeta en "seguidos" sin tener que
  // volver a buscarla.
  image?: string;
}

export interface WatchlistDeal {
  id: string;
  name: string;
  steam: SteamPrice | null;
  nintendo: NintendoCheck;
  // Solo presente cuando el id es un seguido de tipo "pc": precio
  // actual de ESE juego en ESA tienda (buscado por su gameID, no por
  // nombre).
  pc?: {
    available: boolean;
    name?: string;
    thumb?: string;
    currency?: string;
    initial_price?: number;
    final_price?: number;
    discount_percent?: number;
    deal_url?: string;
  };
  // Epic/GOG encontrados (por nombre si el seguido es Steam/Nintendo, o
  // por gameID -- la otra tienda PC -- si el seguido es "pc") -- para
  // poder comparar en "seguidos" contra TODAS las tiendas, no solo la
  // que usaste para seguirlo.
  pc_stores?: PcStoreDeal[];
  // Solo presente cuando el id es un seguido de tipo "pc" Y CheapShark
  // conoce su steamAppID -- con esto se puede cruzar también contra
  // Steam/Switch (ver "steam"/"nintendo" arriba, que en ese caso salen
  // completados usando este appid).
  steam_appid?: number;
}

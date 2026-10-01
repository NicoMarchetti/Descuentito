import type {
  CompareResponse,
  MatchedEntry,
  NintendoDirectResult,
  PcStoreDeal,
  SteamResult,
} from "../lib/types";
import { OfferGrid, STORE_META, type Offer, type StoreKey, type WatchTarget } from "./OfferGrid";

export { STORE_META };
export type { StoreKey, WatchTarget };

interface Props {
  data: CompareResponse;
  onWatch: (target: WatchTarget) => void;
  watchedIds: Set<string>;
  usdToArs?: number;
  /** "all" (o no venir) muestra todas las tiendas; una StoreKey puntual muestra solo esa. */
  storeFilter?: StoreKey | "all";
}

export function CompareTable({ data, onWatch, watchedIds, usdToArs, storeFilter }: Props) {
  const allOffers = buildOffers(data);
  const offers =
    storeFilter && storeFilter !== "all"
      ? allOffers.filter((o) => o.store === storeFilter)
      : allOffers;

  return (
    <section className="mb-10">
      <div className="mb-3 font-mono text-xs tracking-wide text-base-content/50">
        RESULTADOS PARA "{data.query}" ({offers.length})
      </div>

      <OfferGrid
        offers={offers}
        watchedIds={watchedIds}
        onWatch={onWatch}
        usdToArs={usdToArs}
        emptyMessage={
          allOffers.length === 0
            ? "Sin resultados."
            : "No hay ofertas para las tiendas que tenés activas."
        }
      />
    </section>
  );
}

// Arma una tarjeta POR CADA oferta de CADA tienda (en vez de una tarjeta
// por juego con varios precios adentro) -- un juego que está en Steam y
// Switch sale como dos tarjetas separadas, cada una con el color e
// indicador de su tienda. TODAS las tarjetas llevan tanto "+ seguir"
// como "ver oferta" (ver OfferGrid: si a una le faltara cualquiera de
// los dos, no se muestra ninguno, para no mezclar tarjetas con
// distintas acciones disponibles).
function buildOffers(data: CompareResponse): Offer[] {
  const offers: Offer[] = [];

  function steamOffer(s: SteamResult, name: string): Offer {
    return {
      id: `steam:${s.appid}`,
      store: "steam",
      name,
      image: s.tiny_image,
      available: !!s.price?.available,
      currency: s.price?.currency,
      finalPrice: s.price?.final_price,
      initialPrice: s.price?.initial_price,
      discountPercent: s.price?.discount_percent,
      isFree: s.price?.is_free,
      unavailableReason: s.price?.reason,
      watchTarget: { kind: "steam", appid: s.appid, name, image: s.tiny_image },
      dealUrl: `https://store.steampowered.com/app/${s.appid}`,
    };
  }

  function switchOfferFromMatched(m: MatchedEntry): Offer {
    return {
      id: `switch:${m.steam.appid}`,
      store: "switch",
      name: m.name,
      image: m.steam.tiny_image,
      available: true,
      currency: m.nintendo.currency,
      finalPrice: m.nintendo.price,
      discountPercent: m.nintendo.discount_percent,
      switch2: m.nintendo.switch2,
      watchTarget: {
        kind: "steam",
        appid: m.steam.appid,
        name: m.name,
        image: m.steam.tiny_image,
      },
      dealUrl: `https://www.dekudeals.com/app/${m.steam.appid}`,
    };
  }

  function switchOfferDirect(d: NintendoDirectResult): Offer {
    return {
      id: `switch:${d.slug}`,
      store: "switch",
      name: d.name,
      image: d.image,
      available: true,
      currency: d.currency,
      finalPrice: d.price,
      switch2: d.switch2,
      watchTarget: { kind: "nintendo", slug: d.slug, name: d.name, image: d.image },
      dealUrl: `https://www.dekudeals.com/items/${d.slug}`,
    };
  }

  function pcOffer(p: PcStoreDeal): Offer {
    const store: StoreKey = p.store === "GOG" ? "gog" : "epic";
    return {
      id: `${store}:${p.deal_url}`,
      store,
      name: p.name,
      image: p.thumb,
      available: true,
      currency: p.currency,
      finalPrice: p.final_price,
      initialPrice: p.initial_price,
      discountPercent: p.discount_percent,
      dealUrl: p.deal_url,
      // Sin game_id (caso raro) la tarjeta queda sin ninguno de los dos
      // botones (ver OfferGrid): no se puede seguir algo que no se
      // puede volver a identificar después si deja de salir en esta
      // búsqueda por nombre.
      watchTarget: p.game_id
        ? { kind: "pc", store, gameId: p.game_id, name: p.name, image: p.thumb }
        : undefined,
    };
  }

  for (const m of data.matched) {
    offers.push(steamOffer(m.steam, m.name));
    if (m.nintendo.on_switch) offers.push(switchOfferFromMatched(m));
  }

  for (const s of data.steam_only) {
    offers.push(steamOffer(s, s.name));
  }

  for (const d of data.nintendo_direct ?? []) {
    if (d.available) offers.push(switchOfferDirect(d));
  }

  for (const p of data.pc_stores) {
    offers.push(pcOffer(p));
  }

  // Lo que tiene más descuento primero; dentro del mismo %, orden alfabético.
  offers.sort((a, b) => {
    const diff = (b.discountPercent ?? 0) - (a.discountPercent ?? 0);
    if (diff !== 0) return diff;
    return a.name.localeCompare(b.name, "es");
  });

  return offers;
}

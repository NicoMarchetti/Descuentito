import type { CompareResponse, SteamResult } from "../lib/types";
import { STORE_META, watchKey, type StoreKey, type WatchTarget } from "./OfferGrid";

/**
 * UNA tarjeta = UNA oferta puntual (un juego, en UNA tienda, a un precio).
 * Si el mismo juego está en Steam Y en Epic, salen DOS tarjetas separadas
 * -- a diferencia de GameGroupGrid (que junta todo en una sola tarjeta con
 * un renglón por tienda). El usuario pidió este modelo para la grilla de
 * inicio/búsqueda porque es más parecido a su prototipo de Figma; la
 * comparación cruzada entre tiendas para un mismo juego sigue viva en la
 * pestaña "seguidos" (WatchlistPanel sigue usando GameGroupGrid).
 */
export interface OfferCard {
  id: string;
  name: string;
  image?: string;
  store: StoreKey;
  available: boolean;
  currency?: string;
  finalPrice?: number;
  initialPrice?: number;
  discountPercent?: number;
  isFree?: boolean;
  unavailableReason?: string;
  dealUrl?: string;
  watchTarget?: WatchTarget;
}

function steamCard(s: SteamResult): OfferCard {
  return {
    id: `steam:${s.appid}`,
    name: s.name,
    image: s.tiny_image,
    store: "steam",
    available: !!s.price?.available,
    currency: s.price?.currency,
    finalPrice: s.price?.final_price,
    initialPrice: s.price?.initial_price,
    discountPercent: s.price?.discount_percent,
    isFree: s.price?.is_free,
    unavailableReason: s.price?.reason,
    dealUrl: `https://store.steampowered.com/app/${s.appid}`,
    watchTarget: { kind: "steam", appid: s.appid, name: s.name, image: s.tiny_image },
  };
}

// Mismo orden en que el backend mandó todo (data.matched, después
// steam_only, nintendo_direct, pc_stores) -- ninguna tarjeta se reordena
// acá, por la misma razón que CompareTable ya no reordena GameGroups (ver
// el comentario ahí): el orden final lo decide /api/home o /api/compare.
export function buildOfferCards(data: CompareResponse): OfferCard[] {
  const cards: OfferCard[] = [];

  for (const m of data.matched) {
    cards.push(steamCard(m.steam));
    if (m.nintendo.on_switch) {
      cards.push({
        id: `switch:${m.steam.appid}`,
        name: m.name,
        image: m.steam.tiny_image,
        store: "switch",
        available: true,
        currency: m.nintendo.currency,
        finalPrice: m.nintendo.price,
        discountPercent: m.nintendo.discount_percent,
        dealUrl: `https://www.dekudeals.com/app/${m.steam.appid}`,
        // Sin slug propio acá (solo sabemos que "está en switch" vía
        // DekuDeals, no con qué identificador) -- no se puede seguir
        // este renglón puntual, igual que en el modelo agrupado viejo.
      });
    }
  }

  for (const s of data.steam_only) {
    cards.push(steamCard(s));
  }

  for (const d of data.nintendo_direct ?? []) {
    if (!d.available) continue;
    cards.push({
      id: `switch:${d.slug}`,
      name: d.name,
      image: d.image,
      store: "switch",
      available: true,
      currency: d.currency,
      finalPrice: d.price,
      dealUrl: `https://www.dekudeals.com/items/${d.slug}`,
      watchTarget: { kind: "nintendo", slug: d.slug, name: d.name, image: d.image },
    });
  }

  for (const p of data.pc_stores) {
    const store: StoreKey = p.store === "GOG" ? "gog" : "epic";
    cards.push({
      id: `${store}:${p.deal_url}`,
      name: p.name,
      image: p.thumb,
      store,
      available: true,
      currency: p.currency,
      finalPrice: p.final_price,
      initialPrice: p.initial_price,
      discountPercent: p.discount_percent,
      dealUrl: p.deal_url,
      watchTarget: p.game_id
        ? { kind: "pc", store, gameId: p.game_id, name: p.name, image: p.thumb }
        : undefined,
    });
  }

  return cards;
}

function formatPrice(value: number, currency: string): string {
  try {
    return new Intl.NumberFormat("es-AR", {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(value);
  } catch {
    return `${currency} ${value.toFixed(0)}`;
  }
}

interface CardProps {
  offer: OfferCard;
  watching: boolean;
  onWatch: (target: WatchTarget) => void;
  usdToArs?: number;
}

function OfferCardView({ offer, watching, onWatch, usdToArs }: CardProps) {
  const meta = STORE_META[offer.store];
  const convert = !!usdToArs && offer.currency === "USD";
  const free = offer.isFree || (offer.available && offer.finalPrice === 0);
  const hasDiscount = !!offer.discountPercent && offer.initialPrice !== offer.finalPrice;

  return (
    <div className="group relative flex flex-col overflow-hidden border border-base-300 bg-base-200 shadow-sm transition hover:shadow-md">
      <div className="relative aspect-[16/9] w-full bg-base-100">
        {offer.image ? (
          <img
            src={offer.image}
            alt={offer.name}
            loading="lazy"
            // object-contain (no "cover"): se ve la imagen ENTERA, sin
            // recortarla -- aunque la fuente no sea 16:9 (los íconos
            // chicos de alguna tienda, por ejemplo) y quede una franja
            // vacía arriba/abajo o a los costados en vez de recortar
            // parte de la imagen real.
            className="h-full w-full object-contain"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center font-mono text-xs text-base-content/40">
            sin imagen
          </div>
        )}

        {/* Insignia de descuento arriba a la izquierda, como en el mockup. */}
        {hasDiscount && (
          <span className="badge badge-success absolute left-2 top-2 border-none font-mono font-bold">
            -{offer.discountPercent}%
          </span>
        )}

        {/* Ícono de la tienda arriba a la derecha (reemplaza al renglón de
            tienda que tenía la tarjeta agrupada vieja). */}
        <span
          className="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-full p-1 shadow"
          style={{ backgroundColor: meta.bg }}
          title={meta.label}
        >
          <img src={meta.icon} alt={meta.label} className="h-full w-full object-contain" />
        </span>

        {/* Corazón de seguir/siguiendo, flotando sobre la imagen abajo a
            la derecha -- solo si esta oferta puntual se puede seguir. */}
        {offer.watchTarget && (
          <button
            type="button"
            onClick={() => onWatch(offer.watchTarget!)}
            disabled={watching}
            title={watching ? "Ya lo seguís" : "Seguir este juego"}
            aria-label={watching ? "Ya lo seguís" : "Seguir este juego"}
            className={`absolute bottom-2 right-2 flex h-8 w-8 items-center justify-center rounded-full shadow transition ${
              watching ? "bg-success text-success-content" : "bg-base-100/90 text-base-content hover:bg-base-100"
            }`}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill={watching ? "currentColor" : "none"}
              stroke="currentColor"
              strokeWidth="2"
              className="h-4 w-4"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 21s-6.7-4.35-9.33-8.2C.86 9.94 1.6 6.4 4.6 5.03c2.1-.96 4.3-.2 5.6 1.57a.97.97 0 0 0 1.6 0c1.3-1.77 3.5-2.53 5.6-1.57 3 1.37 3.74 4.9 1.93 7.77C18.7 16.65 12 21 12 21Z"
              />
            </svg>
          </button>
        )}
      </div>

      <div className="flex flex-1 flex-col gap-1 p-3">
        <span className="font-mono text-[0.65rem] uppercase tracking-wide text-base-content/40">
          {meta.label}
        </span>
        <h3 className="line-clamp-2 min-h-[2.5em] text-sm font-semibold leading-snug">
          {offer.name}
        </h3>

        <div className="mt-1 flex flex-1 items-end justify-between gap-2">
          <div className="flex flex-col gap-0.5 font-mono text-sm">
            {!offer.available && (
              <span className="text-base-content/40">
                {offer.unavailableReason ?? "no disponible"}
              </span>
            )}
            {offer.available && free && <span className="font-bold text-accent">GRATIS</span>}
            {offer.available && !free && offer.currency && offer.finalPrice !== undefined && (
              <>
                <span className="text-base font-bold">
                  {convert
                    ? formatPrice(offer.finalPrice * usdToArs!, "ARS")
                    : formatPrice(offer.finalPrice, offer.currency)}
                </span>
                <div className="flex items-center gap-1.5">
                  {hasDiscount && offer.initialPrice !== undefined && (
                    <span className="text-xs text-base-content/40 line-through">
                      {convert
                        ? formatPrice(offer.initialPrice * usdToArs!, "ARS")
                        : formatPrice(offer.initialPrice, offer.currency)}
                    </span>
                  )}
                  {convert && (
                    <span className="text-[0.65rem] text-base-content/40">
                      ({formatPrice(offer.finalPrice, "USD")})
                    </span>
                  )}
                </div>
              </>
            )}
          </div>

          {offer.dealUrl && (
            <a
              href={offer.dealUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-solid btn-secondary btn-xs shrink-0"
            >
              Ver oferta
            </a>
          )}
        </div>
      </div>
    </div>
  );
}

interface GridProps {
  offers: OfferCard[];
  watchedIds: Set<string>;
  onWatch: (target: WatchTarget) => void;
  usdToArs?: number;
  emptyMessage?: string;
}

export function OfferCardGrid({ offers, watchedIds, onWatch, usdToArs, emptyMessage }: GridProps) {
  if (offers.length === 0) {
    return (
      <div className="py-6 text-sm text-base-content/40">
        {emptyMessage ?? "Sin resultados."}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
      {offers.map((offer) => {
        const watching = !!offer.watchTarget && watchedIds.has(watchKey(offer.watchTarget));
        return (
          <OfferCardView
            key={offer.id}
            offer={offer}
            watching={watching}
            onWatch={onWatch}
            usdToArs={usdToArs}
          />
        );
      })}
    </div>
  );
}

import { useEffect, useState } from "react";
import type { CompareResponse } from "../lib/types";
import { buildOfferCards, type OfferCard } from "./OfferCardGrid";
import { STORE_META, watchKey, type StoreKey, type WatchTarget } from "./OfferGrid";

interface Props {
  data: CompareResponse;
  onWatch: (target: WatchTarget) => void;
  watchedIds: Set<string>;
  usdToArs?: number;
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

interface FeaturedGroup {
  id: string;
  name: string;
  image?: string;
  /** appid de Steam del juego, si alguna de sus ofertas vino de ahí --
   * con esto se arma una imagen de mejor calidad (ver heroImageUrl) en
   * vez de usar el capsule chico que manda la API de Steam. */
  steamAppId?: number;
  /** Todas las ofertas de este juego (una por tienda) -- se muestran TODAS
   * las insignias de tienda juntas, en vez de una tarjeta separada por
   * tienda para el mismo juego. */
  offers: OfferCard[];
}

// Agrupa por juego -- si el mismo nombre viene de Steam Y de Switch (como
// pasaba con "Outward Definitive Edition"), antes salían DOS slides
// separados repitiendo la misma imagen; ahora es UN slide con las dos
// insignias de tienda juntas arriba.
function buildFeaturedGroups(data: CompareResponse): FeaturedGroup[] {
  const groups = new Map<string, FeaturedGroup>();
  for (const o of buildOfferCards(data)) {
    const key = o.name.trim().toLowerCase();
    let g = groups.get(key);
    if (!g) {
      g = { id: key, name: o.name, image: o.image, offers: [] };
      groups.set(key, g);
    }
    if (!g.image && o.image) g.image = o.image;
    if (!g.steamAppId && o.watchTarget?.kind === "steam") g.steamAppId = o.watchTarget.appid;
    g.offers.push(o);
  }
  return Array.from(groups.values());
}

// Nombres normalizados (mismo criterio que el "id" de GameGroup en
// CompareTable: trim + lowercase) de los juegos que salen en
// "Destacados" -- App.tsx los usa para NO repetirlos en la grilla de
// abajo. Mostrar un juego grande arriba y la MISMA tarjeta chica después
// en la lista general le hacía perder la gracia a la sección.
export function featuredGameNames(data: CompareResponse): Set<string> {
  return new Set(buildFeaturedGroups(data).map((g) => g.id));
}

// El capsule que manda la API de Steam (store search / featuredcategories)
// es chico (~460px de ancho) y se ve borroso agrandado. Steam aloja, para
// casi todos los juegos con página en la tienda, un banner más grande
// (1920x620) en esta misma URL predecible -- se arma directo con el appid
// en vez de depender de lo que haya mandado cada API. Si un juego puntual
// no lo tiene (404), el onError de abajo cae de nuevo a la imagen chica en
// vez de romper.
function heroImageUrl(appid: number): string {
  return `https://cdn.cloudflare.steamstatic.com/steam/apps/${appid}/library_hero.jpg`;
}

// Cuántos juegos destacados se ven a la vez -- antes era 1 solo (una
// tarjeta gigante tipo "banner de la semana"), ahora van en fila, más
// parecido a cómo Steam muestra su propia portada ("REBAJAS DE OTOÑO" con
// varios juegos visibles a la vez y flechas para pasar a los siguientes).
// 2 (no 3): el backend garantiza un MÍNIMO de 6 destacados (ver
// MIN_DESTACADOS en home_candidates/app.py), pero pocas veces hay más que
// eso -- con páginas de 3, 6 resultados sobraban justo para que la
// ÚLTIMA página quedara con 1 sola tarjeta sola y un hueco al lado, como
// si "se hubiera quedado sin más". Con páginas de 2, el mínimo garantizado
// (6) entra exacto en 3 páginas completas.
const PAGE_SIZE = 2;

// "DESTACADOS": hasta 3 páginas de 2 juegos (6 en total) con descuento
// activo, con flechas + puntitos para pasar de página y auto-avance cada
// 6s (se detiene si el mouse está encima).
export function FeaturedCarousel({ data, onWatch, watchedIds, usdToArs }: Props) {
  const groups = buildFeaturedGroups(data).slice(0, PAGE_SIZE * 3);
  const pageCount = Math.ceil(groups.length / PAGE_SIZE);
  const [page, setPage] = useState(0);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    setPage(0);
  }, [groups.length]);

  useEffect(() => {
    if (pageCount <= 1 || paused) return;
    const t = setInterval(() => {
      setPage((p) => (p + 1) % pageCount);
    }, 6000);
    return () => clearInterval(t);
  }, [pageCount, paused]);

  if (groups.length === 0) return null;

  const visible = groups.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);

  function go(delta: number) {
    setPage((p) => (p + delta + pageCount) % pageCount);
  }

  return (
    <section
      className="relative mb-10"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <div className="mb-3 flex items-center justify-between">
        <span className="font-mono text-xs uppercase tracking-wide text-primary">
          — Destacados
        </span>

        {pageCount > 1 && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              aria-label="Destacados anteriores"
              onClick={() => go(-1)}
              className="btn btn-outline btn-xs"
            >
              ‹
            </button>
            <div className="flex gap-1">
              {Array.from({ length: pageCount }).map((_, i) => (
                <button
                  key={i}
                  type="button"
                  aria-label={`Ir a la página ${i + 1} de destacados`}
                  onClick={() => setPage(i)}
                  className={`h-1.5 w-1.5 rounded-full ${i === page ? "bg-primary" : "bg-base-300"}`}
                />
              ))}
            </div>
            <button
              type="button"
              aria-label="Siguientes destacados"
              onClick={() => go(1)}
              className="btn btn-outline btn-xs"
            >
              ›
            </button>
          </div>
        )}
      </div>

      {/* grid fijo de 2 columnas (1 en mobile) -- cada tarjeta reserva su
          alto (aspect-[16/9], mismo criterio que el resto del sitio) así
          cambiar de página no hace saltar el layout. */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {visible.map((group) => (
          <FeaturedCard
            key={group.id}
            group={group}
            onWatch={onWatch}
            watchedIds={watchedIds}
            usdToArs={usdToArs}
          />
        ))}
      </div>
    </section>
  );
}

interface CardProps {
  group: FeaturedGroup;
  onWatch: (target: WatchTarget) => void;
  watchedIds: Set<string>;
  usdToArs?: number;
}

function FeaturedCard({ group, onWatch, watchedIds, usdToArs }: CardProps) {
  // Para precio/link/seguir se usa la oferta con mayor descuento del
  // grupo (si hay varias tiendas, mostramos la mejor) -- el resto de las
  // tiendas igual aparece como insignia arriba.
  const primary = group.offers.reduce((best, o) =>
    (o.discountPercent ?? 0) > (best.discountPercent ?? 0) ? o : best,
  );
  const stores = Array.from(new Set(group.offers.map((o) => o.store)));
  const convert = !!usdToArs && primary.currency === "USD";
  const hasDiscount = !!primary.discountPercent && primary.initialPrice !== primary.finalPrice;
  const watching = !!primary.watchTarget && watchedIds.has(watchKey(primary.watchTarget));

  return (
    <div className="overflow-hidden border border-base-300 bg-base-200 shadow-sm">
      <div className="relative aspect-[16/9] w-full bg-base-300">
        {group.image ? (
          <img
            key={group.id}
            src={group.steamAppId ? heroImageUrl(group.steamAppId) : group.image}
            alt={group.name}
            className="h-full w-full object-cover object-top"
            onError={(e) => {
              // El banner grande no existe para este juego (404) -- cae
              // de nuevo al capsule chico que ya habíamos conseguido, en
              // vez de mostrar una imagen rota. Solo reintenta UNA vez.
              const img = e.currentTarget;
              if (img.src !== group.image && group.image) img.src = group.image;
            }}
          />
        ) : (
          <div className="h-full w-full" />
        )}
      </div>

      <div className="flex flex-col gap-1.5 p-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="badge badge-accent badge-sm border-none font-mono text-[0.6rem] font-bold uppercase tracking-wide">
            Destacado
          </span>
          {stores.map((store) => {
            const meta = STORE_META[store as StoreKey];
            return (
              <span
                key={store}
                className="flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase tracking-wide"
                style={{ backgroundColor: meta.bg, color: meta.fg }}
              >
                <img src={meta.icon} alt="" className="h-3 w-3 object-contain" />
                {meta.label}
              </span>
            );
          })}
          {hasDiscount && (
            <span className="badge badge-success badge-sm border-none font-mono font-bold">
              -{primary.discountPercent}%
            </span>
          )}
        </div>

        <h3 className="truncate text-sm font-bold leading-tight">{group.name}</h3>

        <div className="flex flex-wrap items-center gap-2">
          {primary.available && primary.currency && primary.finalPrice !== undefined && (
            <div className="flex items-baseline gap-1.5 font-mono">
              <span className="text-sm font-bold">
                {convert
                  ? formatPrice(primary.finalPrice * usdToArs!, "ARS")
                  : formatPrice(primary.finalPrice, primary.currency)}
              </span>
              {hasDiscount && primary.initialPrice !== undefined && (
                <span className="text-xs text-base-content/40 line-through">
                  {convert
                    ? formatPrice(primary.initialPrice * usdToArs!, "ARS")
                    : formatPrice(primary.initialPrice, primary.currency)}
                </span>
              )}
            </div>
          )}

          {primary.dealUrl && (
            <a
              href={primary.dealUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-solid btn-secondary btn-xs"
            >
              ▶ Ver oferta
            </a>
          )}

          {primary.watchTarget && (
            <button
              type="button"
              onClick={() => onWatch(primary.watchTarget!)}
              disabled={watching}
              className={`btn btn-xs ${watching ? "btn-success" : "btn-outline"}`}
            >
              {watching ? "siguiendo" : "seguir"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

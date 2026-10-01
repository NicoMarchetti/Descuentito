import { PriceCell } from "./PriceCell";

export type WatchTarget =
  | { kind: "steam"; appid: number; name: string; image?: string }
  | { kind: "nintendo"; slug: string; name: string; image?: string }
  | { kind: "pc"; store: "epic" | "gog"; gameId: string; name: string; image?: string };

export type StoreKey = "steam" | "switch" | "epic" | "gog";

// Colores pedidos por tienda. GOG es un lila claro, así que usa texto
// oscuro para que no se pierda el contraste; el resto va con texto blanco.
export const STORE_META: Record<StoreKey, { label: string; bg: string; fg: string }> = {
  steam: { label: "Steam", bg: "#232934", fg: "#ffffff" },
  switch: { label: "Switch", bg: "#cc000eff", fg: "#ffffff" },
  epic: { label: "Epic Games", bg: "#000000", fg: "#ffffff" },
  gog: { label: "GOG", bg: "#983ffdff", fg: "#1A1A1A" },
};

export interface Offer {
  id: string;
  store: StoreKey;
  name: string;
  image?: string;
  available: boolean;
  currency?: string;
  finalPrice?: number;
  initialPrice?: number;
  discountPercent?: number;
  isFree?: boolean;
  unavailableReason?: string;
  switch2?: boolean;
  watchTarget?: WatchTarget;
  dealUrl?: string;
  /** Si viene, la tarjeta suma "sacar" (pestaña de seguidos). "ver oferta" se muestra igual si hay dealUrl. */
  onRemove?: () => void;
}

interface Props {
  offers: Offer[];
  watchedIds: Set<string>;
  onWatch: (target: WatchTarget) => void;
  usdToArs?: number;
  emptyMessage?: string;
}

export function watchKey(t: WatchTarget): string {
  if (t.kind === "steam") return `steam:${t.appid}`;
  if (t.kind === "nintendo") return `nintendo:${t.slug}`;
  return `pc:${t.store}:${t.gameId}`;
}

// Grilla de tarjetas de oferta -- UNA tarjeta por cada oferta de cada
// tienda, con franja de color e indicador de a qué tienda pertenece.
// La comparten CompareTable (resultados de búsqueda / inicio) y
// WatchlistPanel (tus seguidos), para que ambas pantallas se vean y se
// comporten exactamente igual.
export function OfferGrid({ offers, watchedIds, onWatch, usdToArs, emptyMessage }: Props) {
  if (offers.length === 0) {
    return (
      <div className="py-6 text-sm text-base-content/40">
        {emptyMessage ?? "Sin resultados."}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
      {offers.map((o) => {
        const meta = STORE_META[o.store];
        const key = o.watchTarget ? watchKey(o.watchTarget) : undefined;
        const watching = !!key && watchedIds.has(key);

        return (
          <div
            key={o.id}
            style={{ backgroundColor: meta.bg, color: meta.fg }}
            className="card overflow-hidden border border-base-300 bg-base-200 shadow-sm"
          >
            <div
              className="flex items-center justify-between px-3 py-1"

            >
              <span className="text-[0.65rem] font-bold uppercase tracking-wide">
                {meta.label}
              </span>




            </div>

            <figure className="bg-base-100">
              {o.image ? (
                <img
                  src={o.image}
                  alt={o.name}
                  loading="lazy"
                  className="h-auto w-full"
                />
              ) : (
                <div className="flex h-[90px] w-full items-center justify-center font-mono text-xs text-base-content/40">
                  sin imagen
                </div>
              )}
            </figure>

            <div className="card-body gap-2 p-3">
              <h3 className="card-title line-clamp-2 text-sm leading-snug">
                {o.name}
              </h3>

              <PriceCell
                available={o.available}
                currency={o.currency}
                finalPrice={o.finalPrice}
                initialPrice={o.initialPrice}
                discountPercent={o.discountPercent}
                isFree={o.isFree}
                unavailableReason={o.unavailableReason}
                usdToArs={usdToArs}
              />

              {(() => {
                // "+ seguir" y "ver oferta" van SIEMPRE juntos en el
                // listado principal: si a una oferta le falta cualquiera
                // de los dos (por ej. no hay link directo a la tienda, o
                // esa tienda no soporta seguimiento todavía), no se
                // muestra ninguno de los dos en vez de mostrar uno solo.
                // "sacar" es aparte (pestaña de seguidos) y siempre sale
                // si viene, junto con "ver oferta" si hay link.
                const followPair = !!o.watchTarget && !!o.dealUrl;
                const showDeal = !!o.dealUrl && (o.onRemove || followPair);

                return (
                  <div className="mt-1 flex flex-wrap gap-2">
                    {o.onRemove && (
                      <button
                        className="btn btn-outline btn-error btn-xs"
                        onClick={o.onRemove}
                      >
                        sacar
                      </button>
                    )}
                    {!o.onRemove && followPair && (
                      <button
                        className="btn btn-solid btn-success btn-xs"
                        disabled={watching}
                        onClick={() => onWatch(o.watchTarget!)}
                      >
                        {watching ? "siguiendo" : "+ seguir"}
                      </button>
                    )}
                    {showDeal && (
                      <a
                        href={o.dealUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="btn btn-solid btn-secondary btn-xs"
                      >
                        ver oferta
                      </a>
                    )}
                  </div>
                );
              })()}
            </div>
          </div>
        );
      })}
    </div>
  );
}

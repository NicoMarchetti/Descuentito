import { PriceCell } from "./PriceCell";
import epicIcon from "../assets/stores/epic.png";
import gogIcon from "../assets/stores/gog.png";
import steamIcon from "../assets/stores/steam.png";
import switchIcon from "../assets/stores/switch.png";

export type WatchTarget =
  | { kind: "steam"; appid: number; name: string; image?: string }
  | { kind: "nintendo"; slug: string; name: string; image?: string }
  | { kind: "pc"; store: "epic" | "gog"; gameId: string; name: string; image?: string };

export type StoreKey = "steam" | "switch" | "epic" | "gog";

// Colores pedidos por tienda, más su ícono (en vez del nombre escrito).
// GOG es un lila claro, así que usa texto/ícono con fondo claro para que
// no se pierda el contraste; el resto va con fondo oscuro.
export const STORE_META: Record<
  StoreKey,
  { label: string; bg: string; fg: string; icon: string }
> = {
  steam: { label: "Steam", bg: "#232934", fg: "#ffffff", icon: steamIcon },
  switch: { label: "Switch", bg: "#cc000eff", fg: "#ffffff", icon: switchIcon },
  epic: { label: "Epic Games", bg: "#000000", fg: "#ffffff", icon: epicIcon },
  gog: { label: "GOG", bg: "#983ffdff", fg: "#1A1A1A", icon: gogIcon },
};

export function watchKey(t: WatchTarget): string {
  if (t.kind === "steam") return `steam:${t.appid}`;
  if (t.kind === "nintendo") return `nintendo:${t.slug}`;
  return `pc:${t.store}:${t.gameId}`;
}

/** Un renglón = el precio de ese juego en UNA tienda puntual. */
export interface GameGroupRow {
  key: string;
  store: StoreKey;
  available: boolean;
  currency?: string;
  finalPrice?: number;
  initialPrice?: number;
  discountPercent?: number;
  isFree?: boolean;
  unavailableReason?: string;
  dealUrl?: string;
}

/**
 * Un juego, con un renglón por cada tienda donde se lo encontró. El botón
 * de arriba es UNO SOLO para todo el juego (no uno por tienda): "+ seguir"
 * (si todavía no lo seguís -- lleva el watchTarget que identifica a ESE
 * juego, el que haga falta según de dónde salió primero) o "sacar" (si ya
 * es un seguido y esta tarjeta es la de la pestaña de seguidos). Nunca
 * los dos a la vez en la misma tarjeta.
 */
export interface GameGroup {
  id: string;
  name: string;
  image?: string;
  rows: GameGroupRow[];
  watchTarget?: WatchTarget;
  onRemove?: () => void;
}

interface Props {
  groups: GameGroup[];
  watchedIds: Set<string>;
  onWatch: (target: WatchTarget) => void;
  usdToArs?: number;
  emptyMessage?: string;
}

// Grilla de tarjetas DE JUEGO -- UNA tarjeta por juego, con la imagen
// SIEMPRE como banner ancho arriba (misma proporción para todas, recortada
// con object-cover) para que se vea bien tanto si la fuente trae una
// imagen grande (los "header" de Steam) como una chica (un ícono/capsule
// de alguna tienda) -- antes, mostrar cada imagen a su tamaño real hacía
// que unas tarjetas salieran gigantes y otras minúsculas. Abajo, nombre +
// un único botón de seguir/sacar, y después un renglón por cada tienda
// donde aparece ese juego y su precio ahí (con el ÍCONO de la tienda en
// vez de su nombre escrito). La comparten CompareTable (resultados de
// búsqueda / inicio) y WatchlistPanel (tus seguidos).
export function GameGroupGrid({ groups, watchedIds, onWatch, usdToArs, emptyMessage }: Props) {
  if (groups.length === 0) {
    return (
      <div className="py-6 text-sm text-base-content/40">
        {emptyMessage ?? "Sin resultados."}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
      {groups.map((g) => {
        const key = g.watchTarget ? watchKey(g.watchTarget) : undefined;
        const watching = !!key && watchedIds.has(key);

        return (
          <div
            key={g.id}
            className="card overflow-hidden border border-base-300 bg-base-200 shadow-sm"
          >
            <div className=" w-full bg-base-100">
              {g.image ? (
                <img
                  src={g.image}
                  alt={g.name}
                  loading="lazy"
                  className="h-full w-full object-cover"
                />
              ) : (
                <div className="flex h-full w-full items-center justify-center font-mono text-xs text-base-content/40">
                  sin imagen
                </div>
              )}
            </div>

            <div className="flex items-start justify-between gap-2 p-3">
              <h3 className="line-clamp-2 min-w-0 flex-1 text-sm font-semibold leading-snug">
                {g.name}
              </h3>

              {g.onRemove && (
                <button
                  className="btn btn-outline btn-error btn-xs shrink-0"
                  onClick={g.onRemove}
                >
                  sacar
                </button>
              )}
              {!g.onRemove && g.watchTarget && (
                <button
                  className="btn btn-solid btn-success btn-xs shrink-0"
                  disabled={watching}
                  onClick={() => onWatch(g.watchTarget!)}
                >
                  {watching ? "siguiendo" : "seguir"}
                </button>
              )}
            </div>

            <div className="divide-y divide-base-300 border-t border-base-300">
              {g.rows.map((row) => {
                const meta = STORE_META[row.store];
                return (
                  <div
                    key={row.key}
                    className="flex items-center gap-2 px-3 py-2"
                    style={{ backgroundColor: meta.bg, color: meta.fg }}
                  >
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full p-1">
                      <img src={meta.icon} alt={meta.label} className="h-full w-full object-contain" />
                    </span>

                    <div className="min-w-0 flex-1">
                      <PriceCell
                        available={row.available}
                        currency={row.currency}
                        finalPrice={row.finalPrice}
                        initialPrice={row.initialPrice}
                        discountPercent={row.discountPercent}
                        isFree={row.isFree}
                        unavailableReason={row.unavailableReason}
                        usdToArs={usdToArs}
                      />
                    </div>

                    {row.dealUrl && (
                      <a
                        href={row.dealUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="btn btn-solid btn-secondary btn-xs shrink-0"
                      >
                        Tienda
                      </a>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

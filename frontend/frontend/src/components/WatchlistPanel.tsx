import { useState } from "react";
import { checkWatchlistDeals } from "../lib/api";
import { getLocalWatchlist, removeLocalWatchlistItem } from "../lib/watchlist";
import type { WatchlistDeal, WatchlistItem } from "../lib/types";
import { PriceCell } from "./PriceCell";

interface Props {
  usdToArs?: number;
}

export function WatchlistPanel({ usdToArs }: Props) {
  const [items, setItems] = useState<WatchlistItem[]>(() => getLocalWatchlist());
  const [deals, setDeals] = useState<WatchlistDeal[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function remove(id: string) {
    setItems(removeLocalWatchlistItem(id));
  }

  async function checkDeals() {
    setChecking(true);
    setError(null);
    try {
      setDeals(await checkWatchlistDeals(items));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  }

  return (
    <>
      <section className="mb-10">
        <div className="mb-3 font-mono text-xs tracking-wide text-base-content/50">
          TU LISTA ({items.length})
        </div>
        {items.length === 0 ? (
          <div className="py-6 text-sm text-base-content/40">
            Todavía no agregaste juegos. Buscá uno en la pestaña "comparar" y
            usá "+ seguir". Se guarda en este navegador.
          </div>
        ) : (
          <div className="divide-y divide-base-300">
            {items.map((i) => (
              <div
                key={i.id}
                className="flex items-center justify-between py-3"
              >
                <span className="text-sm">{i.name}</span>
                <button
                  className="btn btn-ghost btn-xs text-base-content/50 hover:text-error"
                  onClick={() => remove(i.id)}
                >
                  sacar
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {items.length > 0 && (
        <section className="mb-10">
          <button
            className="btn btn-outline btn-primary btn-sm"
            onClick={checkDeals}
            disabled={checking}
          >
            {checking ? "chequeando..." : "chequear ofertas ahora"}
          </button>

          {error && <p className="mt-3 text-sm text-error">{error}</p>}

          {deals !== null && (
            <div className="mt-6">
              <div className="mb-3 font-mono text-xs tracking-wide text-base-content/50">
                EN OFERTA AHORA ({deals.length})
              </div>
              {deals.length === 0 ? (
                <div className="py-6 text-sm text-base-content/40">
                  Ninguno de tu lista está en oferta en este momento.
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
                  {deals.map((d) => (
                    <div
                      key={d.id}
                      className="card border border-base-300 bg-base-200 p-3 shadow-sm"
                    >
                      <h3 className="card-title mb-2 line-clamp-2 text-sm leading-snug">
                        {d.name}
                      </h3>
                      <div className="flex flex-col gap-1.5 border-t border-base-300 pt-2">
                        {d.steam && (
                          <PriceCell
                            storeLabel="STEAM"
                            available={!!d.steam.available}
                            currency={d.steam.currency}
                            finalPrice={d.steam.final_price}
                            initialPrice={d.steam.initial_price}
                            discountPercent={d.steam.discount_percent}
                            isFree={d.steam.is_free}
                            usdToArs={usdToArs}
                          />
                        )}
                        <PriceCell
                          storeLabel="SWITCH AR"
                          available={d.nintendo.on_switch}
                          currency={d.nintendo.currency}
                          finalPrice={d.nintendo.price}
                          discountPercent={d.nintendo.discount_percent}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      )}
    </>
  );
}

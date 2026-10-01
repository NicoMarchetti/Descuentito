import { useEffect, useState } from "react";
import { checkWatchlistDeals } from "../lib/api";
import { addLocalWatchlistItem, getLocalWatchlist, removeLocalWatchlistItem } from "../lib/watchlist";
import type { WatchlistDeal, WatchlistItem } from "../lib/types";
import { OfferGrid, type Offer, type StoreKey, type WatchTarget } from "./OfferGrid";

interface Props {
  usdToArs?: number;
}

export function WatchlistPanel({ usdToArs }: Props) {
  const [items, setItems] = useState<WatchlistItem[]>(() => getLocalWatchlist());
  const [deals, setDeals] = useState<WatchlistDeal[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh(current: WatchlistItem[]) {
    if (current.length === 0) {
      setDeals([]);
      return;
    }
    setChecking(true);
    setError(null);
    try {
      setDeals(await checkWatchlistDeals(current));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  }

  // Carga automática al entrar a la pestaña, igual que la pantalla
  // principal (no hace falta tocar un botón para ver las tarjetas).
  useEffect(() => {
    refresh(items);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function remove(id: string) {
    const next = removeLocalWatchlistItem(id);
    setItems(next);
    setDeals((prev) => prev?.filter((d) => d.id !== id) ?? prev);
  }

  // Desde acá también se puede seguir una tarjeta de Epic/GOG que
  // apareció como comparación de un juego ya seguido por otra tienda
  // (ver buildWatchOffers): se guarda como un item más y se chequea su
  // precio en la próxima actualización.
  function watch(target: WatchTarget) {
    const next = addLocalWatchlistItem(target);
    setItems(next);
    refresh(next);
  }

  const offers = buildWatchOffers(items, deals, remove);
  const watchedIds = new Set(items.map((i) => i.id));

  return (
    <section className="mb-10">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="font-mono text-xs tracking-wide text-base-content/50">
          TUS SEGUIDOS ({items.length})
        </div>
        {items.length > 0 && (
          <button
            className="btn btn-outline btn-primary btn-xs"
            onClick={() => refresh(items)}
            disabled={checking}
          >
            {checking ? "actualizando..." : "actualizar precios"}
          </button>
        )}
      </div>

      {error && <p className="mb-3 text-sm text-error">{error}</p>}

      <OfferGrid
        offers={offers}
        watchedIds={watchedIds}
        onWatch={watch}
        usdToArs={usdToArs}
        emptyMessage='Todavía no seguís ningún juego. Tocá "+ seguir" en cualquier tarjeta de la pantalla principal -- se guarda en este navegador.'
      />
    </section>
  );
}

// Arma las tarjetas de los seguidos con el MISMO formato que la
// pantalla principal: un item seguido por Steam puede salir como
// tarjeta de Steam + tarjeta de Switch (si ese juego también está en
// Switch) + tarjeta(s) de Epic/GOG (si ese nombre aparece ahí), igual
// que un resultado "matched" de /api/compare -- la gracia de "seguidos"
// es justamente poder comparar el mismo juego entre TODAS las tiendas,
// no solo ver el precio de la tienda por la que lo seguiste. La imagen
// de la tarjeta "de origen" (Steam o Switch) sale del item guardado en
// localStorage (se capturó al tocar "+ seguir" en su momento); las de
// Epic/GOG traen su propia imagen. El precio sale de la última consulta
// a /api/watchlist/deals -- mientras no llegó esa respuesta, la tarjeta
// de origen se muestra igual (con imagen y nombre) pero avisando que
// está cargando en vez de "no disponible"; las de Epic/GOG directamente
// no aparecen todavía porque no se puede saber si existen sin la
// respuesta.
function buildWatchOffers(
  items: WatchlistItem[],
  deals: WatchlistDeal[] | null,
  onRemove: (id: string) => void,
): Offer[] {
  const byId = new Map((deals ?? []).map((d) => [d.id, d]));
  const pending = deals === null;

  const offers: Offer[] = [];
  for (const item of items) {
    const d = byId.get(item.id);

    if (item.kind === "steam") {
      offers.push({
        id: `steam:${item.appid}`,
        store: "steam",
        name: item.name,
        image: item.image,
        available: !!d?.steam?.available,
        currency: d?.steam?.currency,
        finalPrice: d?.steam?.final_price,
        initialPrice: d?.steam?.initial_price,
        discountPercent: d?.steam?.discount_percent,
        isFree: d?.steam?.is_free,
        unavailableReason: pending ? "cargando..." : (d?.steam?.reason ?? "no disponible"),
        dealUrl: `https://store.steampowered.com/app/${item.appid}`,
        onRemove: () => onRemove(item.id),
      });

      if (d?.nintendo?.on_switch) {
        offers.push({
          id: `switch:${item.appid}`,
          store: "switch",
          name: item.name,
          image: item.image,
          available: true,
          currency: d.nintendo.currency,
          finalPrice: d.nintendo.price,
          discountPercent: d.nintendo.discount_percent,
          switch2: d.nintendo.switch2,
          dealUrl: `https://www.dekudeals.com/app/${item.appid}`,
          onRemove: () => onRemove(item.id),
        });
      }
    } else if (item.kind === "nintendo") {
      // seguido directo por su slug de DekuDeals (exclusivo, sin Steam)
      offers.push({
        id: `switch:${item.slug}`,
        store: "switch",
        name: item.name,
        image: item.image,
        available: !!d?.nintendo?.on_switch,
        currency: d?.nintendo?.currency,
        finalPrice: d?.nintendo?.price,
        discountPercent: d?.nintendo?.discount_percent,
        switch2: d?.nintendo?.switch2,
        unavailableReason: pending ? "cargando..." : "no disponible",
        dealUrl: `https://www.dekudeals.com/items/${item.slug}`,
        onRemove: () => onRemove(item.id),
      });
    } else {
      // kind === "pc": seguido directo en Epic/GOG por su gameID de CheapShark
      const store = item.store ?? "epic";
      const image = item.image ?? d?.pc?.thumb;
      offers.push({
        id: `pc:${store}:${item.gameId}`,
        store,
        name: item.name,
        image,
        available: !!d?.pc?.available,
        currency: d?.pc?.currency,
        finalPrice: d?.pc?.final_price,
        initialPrice: d?.pc?.initial_price,
        discountPercent: d?.pc?.discount_percent,
        unavailableReason: pending ? "cargando..." : "no disponible",
        dealUrl: d?.pc?.deal_url,
        onRemove: () => onRemove(item.id),
      });

      // CheapShark conoce el steamAppID de este juego (cuando existe) --
      // con eso se arma la comparación contra Steam y, si también está
      // en Switch, contra Switch, igual que pasa con un seguido por
      // Steam. Llevan su propio watchTarget para poder seguirse/sacarse
      // por su cuenta.
      if (d?.steam_appid) {
        const appid = d.steam_appid;
        if (d.steam?.available) {
          offers.push({
            id: `steam:${appid}`,
            store: "steam",
            name: item.name,
            image,
            available: true,
            currency: d.steam.currency,
            finalPrice: d.steam.final_price,
            initialPrice: d.steam.initial_price,
            discountPercent: d.steam.discount_percent,
            isFree: d.steam.is_free,
            watchTarget: { kind: "steam", appid, name: item.name, image },
            dealUrl: `https://store.steampowered.com/app/${appid}`,
          });
        }
        if (d.nintendo?.on_switch) {
          offers.push({
            id: `switch:${appid}`,
            store: "switch",
            name: item.name,
            image,
            available: true,
            currency: d.nintendo.currency,
            finalPrice: d.nintendo.price,
            discountPercent: d.nintendo.discount_percent,
            switch2: d.nintendo.switch2,
            watchTarget: { kind: "steam", appid, name: item.name, image },
            dealUrl: `https://www.dekudeals.com/app/${appid}`,
          });
        }
      }
    }

    // Epic/GOG cruzados por nombre -- comparar el mismo juego contra
    // TODAS las tiendas, no solo la que usaste para seguirlo. Llevan su
    // propio watchTarget (gameID) para poder seguirse/sacarse por su
    // cuenta, independiente del item "de origen" de esta vuelta del loop.
    for (const p of d?.pc_stores ?? []) {
      const store: StoreKey = p.store === "GOG" ? "gog" : "epic";
      offers.push({
        id: `${store}:${item.id}:${p.deal_url}`,
        store,
        name: p.name,
        image: p.thumb,
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
  }

  return offers;
}

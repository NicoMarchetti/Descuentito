import { useEffect, useState } from "react";
import { checkWatchlistDeals } from "../lib/api";
import { getLocalWatchlist, removeLocalWatchlistItem } from "../lib/watchlist";
import type { WatchlistDeal, WatchlistItem } from "../lib/types";
import { GameGroupGrid, type GameGroup, type GameGroupRow, type StoreKey } from "./OfferGrid";

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

  const groups = buildWatchGroups(items, deals, remove);

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

      <GameGroupGrid
        groups={groups}
        // Acá nunca hay nada para seguir (todo lo que se ve ya está
        // seguido -- cada tarjeta trae "onRemove" en vez de watchTarget),
        // así que watchedIds/onWatch no se usan de verdad, pero el
        // componente los pide igual porque lo comparte con CompareTable.
        watchedIds={new Set()}
        onWatch={() => { }}
        usdToArs={usdToArs}
        emptyMessage='Todavía no seguís ningún juego. Tocá "+ seguir" en cualquier tarjeta de la pantalla principal -- se guarda en este navegador.'
      />
    </section>
  );
}

// Arma UN grupo por cada item seguido, con un renglón por tienda donde
// aparece -- un item seguido por Steam puede sumar renglón de Switch (si
// ese juego también está ahí) y renglón(es) de Epic/GOG (si ese nombre
// aparece ahí), igual que un resultado "matched" de /api/compare -- la
// gracia de "seguidos" es justamente poder comparar el mismo juego entre
// TODAS las tiendas, no solo ver el precio de la tienda por la que lo
// seguiste. Como acá el juego ya está seguido, NINGÚN renglón lleva
// watchTarget (no hay "+ seguir" por tienda -- ver el pedido del
// usuario: una vez que seguís el juego, lo que querés es comparar
// precios, no volver a decidir si seguirlo tienda por tienda); el único
// botón de la tarjeta es "sacar", a nivel juego completo (onRemove). La
// imagen y el nombre del grupo salen del item guardado en localStorage
// (se capturó al tocar "+ seguir" en su momento), con fallback a la
// imagen que traiga cualquiera de las tiendas cruzadas si el item
// original no tenía una. El precio sale de la última consulta a
// /api/watchlist/deals -- mientras no llegó esa respuesta, el renglón de
// origen se muestra igual (con la tienda) pero avisando que está
// cargando en vez de "no disponible"; los renglones cruzados
// directamente no aparecen todavía porque no se puede saber si existen
// sin la respuesta.
function buildWatchGroups(
  items: WatchlistItem[],
  deals: WatchlistDeal[] | null,
  onRemove: (id: string) => void,
): GameGroup[] {
  const byId = new Map((deals ?? []).map((d) => [d.id, d]));
  const pending = deals === null;

  const groups: GameGroup[] = [];

  for (const item of items) {
    const d = byId.get(item.id);
    const rows: GameGroupRow[] = [];
    let image = item.image;

    if (item.kind === "steam") {
      rows.push({
        key: `steam:${item.appid}`,
        store: "steam",
        available: !!d?.steam?.available,
        currency: d?.steam?.currency,
        finalPrice: d?.steam?.final_price,
        initialPrice: d?.steam?.initial_price,
        discountPercent: d?.steam?.discount_percent,
        isFree: d?.steam?.is_free,
        unavailableReason: pending ? "cargando..." : (d?.steam?.reason ?? "no disponible"),
        dealUrl: `https://store.steampowered.com/app/${item.appid}`,
      });

      if (d?.nintendo?.on_switch) {
        rows.push({
          key: `switch:${item.appid}`,
          store: "switch",
          available: true,
          currency: d.nintendo.currency,
          finalPrice: d.nintendo.price,
          discountPercent: d.nintendo.discount_percent,
          dealUrl: `https://www.dekudeals.com/app/${item.appid}`,
        });
      }
    } else if (item.kind === "nintendo") {
      // seguido directo por su slug de DekuDeals -- "exclusivo de
      // Nintendo" según cómo lo encontraste, pero DekuDeals solo nos
      // confirma que existe en Switch, no que NO esté en Steam, así que
      // el backend también lo busca por nombre ahí (ver steam_appid).
      rows.push({
        key: `switch:${item.slug}`,
        store: "switch",
        available: !!d?.nintendo?.on_switch,
        currency: d?.nintendo?.currency,
        finalPrice: d?.nintendo?.price,
        discountPercent: d?.nintendo?.discount_percent,
        unavailableReason: pending ? "cargando..." : "no disponible",
        dealUrl: `https://www.dekudeals.com/items/${item.slug}`,
      });

      if (d?.steam_appid && d.steam?.available) {
        const appid = d.steam_appid;
        rows.push({
          key: `steam:${appid}`,
          store: "steam",
          available: true,
          currency: d.steam.currency,
          finalPrice: d.steam.final_price,
          initialPrice: d.steam.initial_price,
          discountPercent: d.steam.discount_percent,
          isFree: d.steam.is_free,
          dealUrl: `https://store.steampowered.com/app/${appid}`,
        });
      }
    } else {
      // kind === "pc": seguido directo en Epic/GOG por su gameID de CheapShark
      const store = item.store ?? "epic";
      image = item.image ?? d?.pc?.thumb;
      rows.push({
        key: `pc:${store}:${item.gameId}`,
        store,
        available: !!d?.pc?.available,
        currency: d?.pc?.currency,
        finalPrice: d?.pc?.final_price,
        initialPrice: d?.pc?.initial_price,
        discountPercent: d?.pc?.discount_percent,
        unavailableReason: pending ? "cargando..." : "no disponible",
        dealUrl: d?.pc?.deal_url,
      });

      // CheapShark conoce el steamAppID de este juego (cuando existe) --
      // con eso se arma la comparación contra Steam y, si también está
      // en Switch, contra Switch, igual que pasa con un seguido por Steam.
      if (d?.steam_appid) {
        const appid = d.steam_appid;
        if (d.steam?.available) {
          rows.push({
            key: `steam:${appid}`,
            store: "steam",
            available: true,
            currency: d.steam.currency,
            finalPrice: d.steam.final_price,
            initialPrice: d.steam.initial_price,
            discountPercent: d.steam.discount_percent,
            isFree: d.steam.is_free,
            dealUrl: `https://store.steampowered.com/app/${appid}`,
          });
        }
        if (d.nintendo?.on_switch) {
          rows.push({
            key: `switch:${appid}`,
            store: "switch",
            available: true,
            currency: d.nintendo.currency,
            finalPrice: d.nintendo.price,
            discountPercent: d.nintendo.discount_percent,
            dealUrl: `https://www.dekudeals.com/app/${appid}`,
          });
        }
      }
    }

    // Epic/GOG cruzados por nombre -- comparar el mismo juego contra
    // TODAS las tiendas, no solo la que usaste para seguirlo.
    for (const p of d?.pc_stores ?? []) {
      const store: StoreKey = p.store === "GOG" ? "gog" : "epic";
      rows.push({
        key: `${store}:${item.id}:${p.deal_url}`,
        store,
        available: true,
        currency: p.currency,
        finalPrice: p.final_price,
        initialPrice: p.initial_price,
        discountPercent: p.discount_percent,
        dealUrl: p.deal_url,
      });
      if (!image) image = p.thumb;
    }

    groups.push({
      id: item.id,
      name: item.name,
      image,
      rows,
      onRemove: () => onRemove(item.id),
    });
  }

  return groups;
}

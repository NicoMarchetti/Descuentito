import type { CompareResponse, SteamResult } from "../lib/types";
import {
  GameGroupGrid,
  STORE_META,
  type GameGroup,
  type GameGroupRow,
  type StoreKey,
  type WatchTarget,
} from "./OfferGrid";

export { STORE_META };
export type { StoreKey, WatchTarget };

interface Props {
  data: CompareResponse;
  onWatch: (target: WatchTarget) => void;
  watchedIds: Set<string>;
  usdToArs?: number;
  /** "all" (o no venir) muestra todas las tiendas; una StoreKey puntual muestra solo esa. */
  storeFilter?: StoreKey | "all";
  /** Encabezado "RESULTADOS PARA ..." -- solo tiene sentido cuando el usuario hizo una búsqueda, no al cargar la pantalla de inicio. */
  searchQuery?: string;
}

export function CompareTable({
  data,
  onWatch,
  watchedIds,
  usdToArs,
  storeFilter,
  searchQuery,
}: Props) {
  const allGroups = buildGameGroups(data);

  // El filtro por tienda no saca tarjetas enteras: dentro de cada juego
  // que SÍ está en esa tienda, deja solo el renglón de esa tienda (las
  // demás comparaciones se ocultan, no tiene sentido mostrarlas si elegiste
  // ver "solo Steam").
  const groups =
    storeFilter && storeFilter !== "all"
      ? allGroups
        .filter((g) => g.rows.some((r) => r.store === storeFilter))
        .map((g) => ({ ...g, rows: g.rows.filter((r) => r.store === storeFilter) }))
      : allGroups;

  return (
    <section className="mb-10">
      {searchQuery && (
        <div className="mb-3 font-mono text-xs tracking-wide text-base-content/50">
          RESULTADOS PARA "{searchQuery}" ({groups.length})
        </div>
      )}

      <GameGroupGrid
        groups={groups}
        watchedIds={watchedIds}
        onWatch={onWatch}
        usdToArs={usdToArs}
        emptyMessage={
          allGroups.length === 0
            ? "Sin resultados."
            : "No hay ofertas para las tiendas que tenés activas."
        }
      />
    </section>
  );
}

// Arma UN grupo por JUEGO (no por oferta): si el mismo nombre aparece en
// Steam, Switch y/o Epic/GOG, todo eso se junta en una sola tarjeta con un
// renglón por tienda, en vez de una tarjeta separada repitiendo la imagen
// por cada precio. El botón "+ seguir" de la tarjeta sigue al JUEGO (no a
// una tienda puntual): usa el primer identificador disponible, en orden
// Steam > Nintendo-directo > Epic/GOG -- el que sea alcanza, porque la
// pestaña de seguidos después cruza ese juego contra TODAS las tiendas
// igual, sea cual sea el que se haya usado para seguirlo (ver
// WatchlistPanel). Agrupa por nombre normalizado, igual que hace el
// backend en /api/home (home_candidates) para no duplicar un juego que
// aparece en más de una fuente.
function buildGameGroups(data: CompareResponse): GameGroup[] {
  const groups = new Map<string, GameGroup>();
  const hasTarget = new Set<string>();

  function getGroup(name: string): GameGroup {
    const key = name.trim().toLowerCase();
    let g = groups.get(key);
    if (!g) {
      g = { id: key, name, rows: [] };
      groups.set(key, g);
    }
    return g;
  }

  function setImage(g: GameGroup, image?: string) {
    if (image && !g.image) g.image = image;
  }

  function setTarget(g: GameGroup, target: WatchTarget) {
    if (!hasTarget.has(g.id)) {
      g.watchTarget = target;
      hasTarget.add(g.id);
    }
  }

  function addRow(g: GameGroup, row: GameGroupRow) {
    if (g.rows.some((r) => r.key === row.key)) return;
    g.rows.push(row);
  }

  function steamRow(s: SteamResult): GameGroupRow {
    return {
      key: `steam:${s.appid}`,
      store: "steam",
      available: !!s.price?.available,
      currency: s.price?.currency,
      finalPrice: s.price?.final_price,
      initialPrice: s.price?.initial_price,
      discountPercent: s.price?.discount_percent,
      isFree: s.price?.is_free,
      unavailableReason: s.price?.reason,
      dealUrl: `https://store.steampowered.com/app/${s.appid}`,
    };
  }

  for (const m of data.matched) {
    const g = getGroup(m.name);
    setImage(g, m.steam.tiny_image);
    addRow(g, steamRow(m.steam));
    setTarget(g, {
      kind: "steam",
      appid: m.steam.appid,
      name: m.name,
      image: m.steam.tiny_image,
    });

    if (m.nintendo.on_switch) {
      addRow(g, {
        key: `switch:${m.steam.appid}`,
        store: "switch",
        available: true,
        currency: m.nintendo.currency,
        finalPrice: m.nintendo.price,
        discountPercent: m.nintendo.discount_percent,
        dealUrl: `https://www.dekudeals.com/app/${m.steam.appid}`,
      });
    }
  }

  for (const s of data.steam_only) {
    const g = getGroup(s.name);
    setImage(g, s.tiny_image);
    addRow(g, steamRow(s));
    setTarget(g, { kind: "steam", appid: s.appid, name: s.name, image: s.tiny_image });
  }

  for (const d of data.nintendo_direct ?? []) {
    if (!d.available) continue;
    const g = getGroup(d.name);
    setImage(g, d.image);
    addRow(g, {
      key: `switch:${d.slug}`,
      store: "switch",
      available: true,
      currency: d.currency,
      finalPrice: d.price,
      dealUrl: `https://www.dekudeals.com/items/${d.slug}`,
    });
    setTarget(g, { kind: "nintendo", slug: d.slug, name: d.name, image: d.image });
  }

  for (const p of data.pc_stores) {
    const store: StoreKey = p.store === "GOG" ? "gog" : "epic";
    const g = getGroup(p.name);
    setImage(g, p.thumb);
    addRow(g, {
      key: `${store}:${p.deal_url}`,
      store,
      available: true,
      currency: p.currency,
      finalPrice: p.final_price,
      initialPrice: p.initial_price,
      discountPercent: p.discount_percent,
      dealUrl: p.deal_url,
    });
    // Sin game_id (caso raro) no se usa como identificador para seguir
    // -- no se puede volver a encontrar ese mismo juego después si deja
    // de salir en esta búsqueda por nombre.
    if (p.game_id) {
      setTarget(g, { kind: "pc", store, gameId: p.game_id, name: p.name, image: p.thumb });
    }
  }

  const list = Array.from(groups.values());

  // Lo que tiene más descuento primero (el mejor renglón de cada
  // tarjeta); dentro del mismo %, orden alfabético.
  list.sort((a, b) => {
    const bestA = a.rows.reduce((max, r) => Math.max(max, r.discountPercent ?? 0), 0);
    const bestB = b.rows.reduce((max, r) => Math.max(max, r.discountPercent ?? 0), 0);
    const diff = bestB - bestA;
    if (diff !== 0) return diff;
    return a.name.localeCompare(b.name, "es");
  });

  return list;
}

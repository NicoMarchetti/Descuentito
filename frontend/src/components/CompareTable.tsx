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
  /** Encabezado "RESULTADOS PARA ..." -- solo tiene sentido cuando el usuario hizo una búsqueda, no al cargar la pantalla de inicio. */
  searchQuery?: string;
  /** Nombres (normalizados, ver featuredGameNames en FeaturedCarousel) a
   * NO mostrar acá -- los juegos que ya salen en "Destacados" arriba no
   * se repiten en la grilla general, si no pierde la gracia de ser
   * "destacado". Solo tiene sentido en la home (en resultados de
   * búsqueda no hay sección de destacados). */
  excludeNames?: Set<string>;
  /** true cuando el filtro "Switch" del sidebar está activo -- a
   * diferencia de steam/epic/gog (que el backend ya filtra antes de
   * paginar), esto filtra del lado del cliente, solo entre lo que YA
   * está cargado (ver el comentario largo en App.tsx sobre por qué). */
  switchOnly?: boolean;
}

/** Mismo filtrado que hace CompareTable (destacados excluidos + Switch si
 * corresponde), pero expuesto aparte para que App.tsx pueda mostrar un
 * contador ("N ofertas encontradas") que coincida con lo que realmente se
 * ve en la grilla -- antes ese número salía de result.total (el total del
 * backend, sin la exclusión de destacados), así que no coincidía con la
 * cantidad real de tarjetas visibles. */
export function visibleGameCount(
  data: CompareResponse,
  options?: { excludeNames?: Set<string>; switchOnly?: boolean },
): number {
  return buildGameGroups(data)
    .filter((g) => !options?.excludeNames?.has(g.id))
    .filter((g) => !options?.switchOnly || g.rows.some((r) => r.store === "switch")).length;
}

export function CompareTable({
  data,
  onWatch,
  watchedIds,
  usdToArs,
  searchQuery,
  excludeNames,
  switchOnly,
}: Props) {
  const groups = buildGameGroups(data)
    .filter((g) => !excludeNames?.has(g.id))
    .filter((g) => !switchOnly || g.rows.some((r) => r.store === "switch"));

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
        emptyMessage="Sin resultados."
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

  // Antes esto procesaba TODO "data.matched" primero y recién después
  // TODO "data.steam_only" -- son dos arrays separados, y como el scroll
  // infinito los va acumulando por separado en cada página (ver
  // handleLoadMore en App.tsx), un juego CON versión de Switch cargado en
  // una página más nueva terminaba arriba de uno SIN Switch que ya se
  // había mostrado desde una página anterior, aunque ese llegó antes: el
  // usuario veía la grilla "reacomodarse" cada vez que cargaba más. Con el
  // índice "order" que manda /api/home (posición real en la lista
  // completa, no por página), se intercalan ambos arrays respetando ese
  // orden real en vez del orden en que los separó la respuesta. En
  // /api/compare (búsqueda manual, sin paginar) "order" no viene -- el
  // fallback (offset grande para steam_only) mantiene el comportamiento
  // de antes ahí, donde nunca fue un problema porque no se acumula nada.
  type SteamEntry =
    | { kind: "matched"; order: number; item: (typeof data.matched)[number] }
    | { kind: "steam_only"; order: number; item: (typeof data.steam_only)[number] };

  const STEAM_ONLY_FALLBACK_OFFSET = 1_000_000;

  const steamEntries: SteamEntry[] = [
    ...data.matched.map(
      (item, i): SteamEntry => ({ kind: "matched", order: item.steam.order ?? i, item }),
    ),
    ...data.steam_only.map(
      (item, i): SteamEntry => ({
        kind: "steam_only",
        order: item.order ?? STEAM_ONLY_FALLBACK_OFFSET + i,
        item,
      }),
    ),
  ];
  steamEntries.sort((a, b) => a.order - b.order);

  for (const entry of steamEntries) {
    if (entry.kind === "matched") {
      const m = entry.item;
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
    } else {
      const s = entry.item;
      const g = getGroup(s.name);
      setImage(g, s.tiny_image);
      addRow(g, steamRow(s));
      setTarget(g, { kind: "steam", appid: s.appid, name: s.name, image: s.tiny_image });
    }
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

  // SIN ordenar nada acá -- el orden final es el orden en que llegaron
  // los juegos desde el backend (data.matched, después data.steam_only,
  // etc., cada uno en el orden en que vino en la respuesta de /api/home
  // o /api/compare), que es justamente lo que pidió el usuario: nada de
  // criterios propios del frontend por encima de eso. `groups` es un Map
  // que ya preserva el orden de la primera vez que se tocó cada juego, así
  // que alcanza con pasarlo a lista sin tocar nada más.
  return Array.from(groups.values());
}

import type { CompareResponse, PcStoreDeal } from "../lib/types";
import { PriceCell } from "./PriceCell";

export type WatchTarget =
  | { kind: "steam"; appid: number; name: string }
  | { kind: "nintendo"; slug: string; name: string };

interface Props {
  data: CompareResponse;
  onWatch: (target: WatchTarget) => void;
  watchedIds: Set<string>;
  usdToArs?: number;
}

interface Row {
  id: string;
  watchTarget: WatchTarget;
  name: string;
  image?: string;
  steam?: CompareResponse["matched"][number]["steam"]["price"];
  nintendo: CompareResponse["matched"][number]["nintendo"];
  epic?: PcStoreDeal;
  gog?: PcStoreDeal;
}

// Convierte un precio a ARS para poder comparar "más barato" entre
// tiendas con distinta moneda -- solo se puede si ya está en ARS, o si
// está en USD y tenemos una cotización activa para convertirlo. Si no,
// devuelve undefined y esa celda simplemente no entra en la comparación
// (no tiene sentido comparar ARS contra USD sin convertir).
function toArs(
  currency: string | undefined,
  price: number | undefined,
  usdToArs: number | undefined,
): number | undefined {
  if (price === undefined || !currency) return undefined;
  if (currency === "ARS") return price;
  if (currency === "USD" && usdToArs) return price * usdToArs;
  return undefined;
}

function isMin(value: number | undefined, min: number | undefined): boolean {
  return value !== undefined && min !== undefined && Math.abs(value - min) < 0.01;
}

export function CompareTable({ data, onWatch, watchedIds, usdToArs }: Props) {
  const rows = buildRows(data);

  return (
    <section className="mb-10">
      <div className="mb-3 font-mono text-xs tracking-wide text-base-content/50">
        RESULTADOS PARA "{data.query}" ({rows.length})
      </div>

      {rows.length === 0 ? (
        <div className="py-6 text-sm text-base-content/40">Sin resultados.</div>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
          {rows.map((row) => {
            const steamArs =
              row.steam?.available && !row.steam.is_free
                ? toArs(row.steam.currency, row.steam.final_price, usdToArs)
                : undefined;
            const nintendoArs = row.nintendo.on_switch
              ? toArs(row.nintendo.currency, row.nintendo.price, usdToArs)
              : undefined;
            const epicArs = row.epic
              ? toArs(row.epic.currency, row.epic.final_price, usdToArs)
              : undefined;
            const gogArs = row.gog
              ? toArs(row.gog.currency, row.gog.final_price, usdToArs)
              : undefined;

            const values = [steamArs, nintendoArs, epicArs, gogArs].filter(
              (v): v is number => v !== undefined,
            );
            const min = values.length > 1 ? Math.min(...values) : undefined;

            return (
              <div
                key={row.id}
                className="card border border-base-300 bg-base-200 shadow-sm"
              >
                <figure className="bg-base-100 p-2">
                  {row.image ? (
                    <img
                      src={row.image}
                      alt={row.name}
                      loading="lazy"
                      className="h-auto w-full rounded"
                    />
                  ) : (
                    <div className="flex h-[90px] w-full items-center justify-center font-mono text-xs text-base-content/40">
                      sin imagen
                    </div>
                  )}
                </figure>

                <div className="card-body gap-2 p-3">
                  <h3 className="card-title line-clamp-2 text-sm leading-snug">
                    {row.name}
                  </h3>

                  <div className="flex flex-col gap-1.5 border-t border-base-300 pt-2">
                    {row.steam !== undefined && (
                      <PriceCell
                        storeLabel="STEAM"
                        available={!!row.steam?.available}
                        currency={row.steam?.currency}
                        finalPrice={row.steam?.final_price}
                        initialPrice={row.steam?.initial_price}
                        discountPercent={row.steam?.discount_percent}
                        isFree={row.steam?.is_free}
                        unavailableReason={row.steam?.reason}
                        usdToArs={usdToArs}
                        isCheapest={isMin(steamArs, min)}
                      />
                    )}
                    <PriceCell
                      storeLabel="SWITCH AR"
                      available={row.nintendo.on_switch}
                      currency={row.nintendo.currency}
                      finalPrice={row.nintendo.price}
                      discountPercent={row.nintendo.discount_percent}
                      isCheapest={isMin(nintendoArs, min)}
                    />
                    {row.steam !== undefined && (
                      <>
                        <PriceCell
                          storeLabel="EPIC"
                          available={!!row.epic}
                          currency={row.epic?.currency}
                          finalPrice={row.epic?.final_price}
                          initialPrice={row.epic?.initial_price}
                          discountPercent={row.epic?.discount_percent}
                          usdToArs={usdToArs}
                          isCheapest={isMin(epicArs, min)}
                        />
                        <PriceCell
                          storeLabel="GOG"
                          available={!!row.gog}
                          currency={row.gog?.currency}
                          finalPrice={row.gog?.final_price}
                          initialPrice={row.gog?.initial_price}
                          discountPercent={row.gog?.discount_percent}
                          usdToArs={usdToArs}
                          isCheapest={isMin(gogArs, min)}
                        />
                      </>
                    )}
                  </div>

                  <button
                    className="btn btn-outline btn-success btn-xs mt-1"
                    disabled={watchedIds.has(row.id)}
                    onClick={() => onWatch(row.watchTarget)}
                  >
                    {watchedIds.has(row.id) ? "siguiendo" : "+ seguir"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// Junta matched + steam_only + nintendo_direct en una sola lista de filas.
// Para lo que vino de Steam, le pega al lado los resultados de Epic/GOG
// que matcheen por nombre exacto (case-insensitive) -- si no matchea
// ninguno, la celda queda vacía, no forzamos un match dudoso. Cada fila
// lleva su watchTarget (steam+appid o nintendo+slug) para que "+ seguir"
// funcione en cualquiera de los dos casos.
function buildRows(data: CompareResponse): Row[] {
  const pcByName = new Map<string, { epic?: PcStoreDeal; gog?: PcStoreDeal }>();
  for (const d of data.pc_stores) {
    const key = d.name.trim().toLowerCase();
    const entry = pcByName.get(key) ?? {};
    if (d.store === "Epic Games Store") entry.epic = d;
    else if (d.store === "GOG") entry.gog = d;
    pcByName.set(key, entry);
  }

  function toRow(
    appid: number,
    name: string,
    image: string | undefined,
    steam: Row["steam"],
    nintendo: Row["nintendo"],
  ): Row {
    const pc = pcByName.get(name.trim().toLowerCase());
    return {
      id: `steam:${appid}`,
      watchTarget: { kind: "steam", appid, name },
      name,
      image: image ?? pc?.epic?.thumb ?? pc?.gog?.thumb,
      steam,
      nintendo,
      epic: pc?.epic,
      gog: pc?.gog,
    };
  }

  const fromMatched: Row[] = data.matched.map((m) =>
    toRow(
      m.steam.appid,
      m.name,
      m.steam.tiny_image,
      m.steam.price,
      m.nintendo,
    ),
  );

  const fromSteamOnly: Row[] = data.steam_only.map((s) =>
    toRow(s.appid, s.name, s.tiny_image, s.price, { on_switch: false }),
  );

  const fromNintendoDirect: Row[] = (data.nintendo_direct ?? []).map((d) => ({
    id: `nintendo:${d.slug}`,
    watchTarget: { kind: "nintendo", slug: d.slug, name: d.name },
    name: d.name,
    image: d.image,
    steam: undefined,
    nintendo: {
      on_switch: d.available,
      currency: d.currency,
      price: d.price,
    },
  }));

  return [...fromMatched, ...fromSteamOnly, ...fromNintendoDirect];
}

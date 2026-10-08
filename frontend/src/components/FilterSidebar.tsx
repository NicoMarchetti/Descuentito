import type { FormEvent } from "react";
import type { DolarRate } from "../lib/types";
import { STORE_META } from "./OfferGrid";

const SIDEBAR_STORES = ["steam", "epic", "gog", "xbox", "switch"] as const;
type SidebarStore = (typeof SIDEBAR_STORES)[number];

const DISCOUNT_TIERS = [50, 70, 90] as const;

interface Props {
  query: string;
  onQueryChange: (v: string) => void;
  onSearch: (e: FormEvent) => void;
  /** true en modo búsqueda: tienda/descuento/precio no hacen nada ahí (solo filtran /api/home), así que se ocultan. */
  hideHomeFilters?: boolean;
  selectedStores: Set<SidebarStore>;
  onToggleStore: (store: SidebarStore) => void;
  minDiscount: number;
  onSetMinDiscount: (tier: number) => void;
  priceMin: string;
  priceMax: string;
  onPriceMinChange: (v: string) => void;
  onPriceMaxChange: (v: string) => void;
  dolares: DolarRate[];
  convertOn: boolean;
  onToggleConvert: () => void;
  dolarCasa: string;
  onChangeDolarCasa: (casa: string) => void;
  onClear: () => void;
}

// Sidebar de filtros calcado del prototipo de Figma: tienda (checkboxes,
// multi-selección), descuento mínimo (un solo umbral a la vez) y rango
// de precio en ARS. A pedido de Nico, sin los números al lado de cada
// opción (cuántas ofertas quedarían) -- prefiere algo más ambiguo, igual
// que el contador general de la grilla (ver ofertasAmbiguas en App.tsx).
export function FilterSidebar({
  query,
  onQueryChange,
  onSearch,
  hideHomeFilters,
  selectedStores,
  onToggleStore,
  minDiscount,
  onSetMinDiscount,
  priceMin,
  priceMax,
  onPriceMinChange,
  onPriceMaxChange,
  dolares,
  convertOn,
  onToggleConvert,
  dolarCasa,
  onChangeDolarCasa,
  onClear,
}: Props) {
  const hasActiveFilters =
    selectedStores.size > 0 || minDiscount > 0 || !!priceMin || !!priceMax;
  const selectedRate = dolares.find((d) => d.casa === dolarCasa);

  return (
    <aside className="w-full shrink-0 border border-base-300 bg-base-200 p-4 lg:w-64">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-bold uppercase tracking-wide">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className="h-4 w-4"
          >
            <path strokeLinecap="round" d="M4 6h16M7 12h10M10 18h4" />
          </svg>
          Filtrar
        </h3>
        {hasActiveFilters && (
          <button
            type="button"
            onClick={onClear}
            className="text-xs font-semibold text-success hover:underline"
          >
            Limpiar
          </button>
        )}
      </div>

      <form onSubmit={onSearch} className="mb-4">
        <label className="input input-bordered flex items-center gap-2 rounded-full bg-base-100 shadow-sm">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className="h-4 w-4 shrink-0 text-base-content/40"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="m21 21-4.35-4.35" strokeLinecap="round" />
          </svg>
          <input
            type="text"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="Buscá un juego..."
            className="w-full grow bg-transparent outline-none"
          />
        </label>
      </form>

      <div className="divider my-2" />

      {dolares.length > 0 && (
        <div className="mb-4">
          <div className="mb-2 font-mono text-[0.65rem] uppercase tracking-wide text-base-content/40">
            Moneda
          </div>
          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="checkbox checkbox-success checkbox-sm"
              checked={convertOn}
              onChange={onToggleConvert}
            />
            Convertir USD a ARS
          </label>
          {convertOn && (
            <div className="mt-2 flex flex-col gap-1">
              <select
                className="select select-bordered select-sm"
                value={dolarCasa}
                onChange={(e) => onChangeDolarCasa(e.target.value)}
              >
                {dolares.map((d) => (
                  <option key={d.casa} value={d.casa}>
                    Dólar {d.nombre}
                  </option>
                ))}
              </select>
              {selectedRate && (
                <span className="text-xs text-base-content/40">
                  ${selectedRate.venta.toLocaleString("es-AR")} por USD
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {!hideHomeFilters && (
        <>
          <div className="divider my-2" />

          <div className="mb-4">
            <div className="mb-2 font-mono text-[0.65rem] uppercase tracking-wide text-base-content/40">
              Tienda
            </div>
            <div className="flex flex-col gap-1.5">
              {SIDEBAR_STORES.map((store) => {
                const meta = STORE_META[store];
                return (
                  <label
                    key={store}
                    className="flex cursor-pointer items-center justify-between gap-2 text-sm"
                    // En Switch el número crece con el scroll en vez de ser un
                    // total fijo del pool completo -- un tooltip nativo alcanza,
                    // no hace falta un ícono aparte para esto.
                    title={store === "switch" ? "Entre los juegos ya cargados" : undefined}
                  >
                    <span className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="checkbox checkbox-success checkbox-sm"
                        checked={selectedStores.has(store)}
                        onChange={() => onToggleStore(store)}
                      />
                      {meta.label}
                    </span>
                  </label>
                );
              })}
            </div>
          </div>

          <div className="divider my-2" />

          <div className="mb-4">
            <div className="mb-2 font-mono text-[0.65rem] uppercase tracking-wide text-base-content/40">
              Descuento mínimo
            </div>
            <div className="flex flex-col gap-1.5">
              {DISCOUNT_TIERS.map((tier) => {
                const active = minDiscount === tier;
                return (
                  <label
                    key={tier}
                    className="flex cursor-pointer items-center justify-between gap-2 text-sm"
                  >
                    <span className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="checkbox checkbox-success checkbox-sm"
                        checked={active}
                        onChange={() => onSetMinDiscount(active ? 0 : tier)}
                      />
                      {tier}% o más
                    </span>
                  </label>
                );
              })}
            </div>
          </div>

          <div className="divider my-2" />

          <div>
            <div className="mb-2 font-mono text-[0.65rem] uppercase tracking-wide text-base-content/40">
              Precio en ARS
            </div>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={0}
                inputMode="numeric"
                placeholder="$ 0"
                value={priceMin}
                onChange={(e) => onPriceMinChange(e.target.value)}
                className="input input-bordered input-sm w-full"
              />
              <span className="text-base-content/30">–</span>
              <input
                type="number"
                min={0}
                inputMode="numeric"
                placeholder="$ 50.000"
                value={priceMax}
                onChange={(e) => onPriceMaxChange(e.target.value)}
                className="input input-bordered input-sm w-full"
              />
            </div>
          </div>
        </>
      )}
    </aside>
  );
}

import { FormEvent, useEffect, useRef, useState } from "react";
import { compare, getDolarRates, home } from "./lib/api";
import { addLocalWatchlistItem, getLocalWatchlist } from "./lib/watchlist";
import type { CompareResponse, DolarRate } from "./lib/types";
import {
  CompareTable,
  STORE_META,
  type StoreKey,
  type WatchTarget,
} from "./components/CompareTable";
import { WatchlistPanel } from "./components/WatchlistPanel";
import { CafecitoButton } from "./components/CafecitoButton";
import { SkeletonGrid } from "./components/SkeletonGrid";

type Tab = "comparar" | "seguimiento";
type Mode = "home" | "search";

const ALL_STORES: StoreKey[] = ["steam", "switch", "epic", "gog"];

export default function App() {
  const [tab, setTab] = useState<Tab>("comparar");
  const [theme, setTheme] = useState<"descuentito" | "descuentito-dark">(
    () =>
      (localStorage.getItem("theme") as "descuentito" | "descuentito-dark") ??
      "descuentito",
  );

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);

  function toggleTheme() {
    setTheme((t) => (t === "descuentito" ? "descuentito-dark" : "descuentito"));
  }

  const [mode, setMode] = useState<Mode>("home");
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<CompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [watchedIds, setWatchedIds] = useState<Set<string>>(
    () => new Set(getLocalWatchlist().map((i) => i.id)),
  );

  // Filtro por tienda: selección única -- al tocar un botón, la grilla
  // muestra SOLO esa tienda. "all" (default) muestra todo.
  const [storeFilter, setStoreFilter] = useState<StoreKey | "all">("all");

  // Disclaimer de impuestos: se puede cerrar y queda cerrado (localStorage)
  // para que no vuelva a aparecer en próximas visitas.
  const [disclaimerDismissed, setDisclaimerDismissed] = useState(
    () => localStorage.getItem("disclaimerDismissed") === "1",
  );
  function dismissDisclaimer() {
    setDisclaimerDismissed(true);
    localStorage.setItem("disclaimerDismissed", "1");
  }

  // Cotizaciones del dólar (oficial/blue/tarjeta/MEP/CCL), para poder
  // mostrar los precios de Epic/GOG (y Steam si viniera en USD) también
  // en pesos. "convertOn" prende/apaga la conversión; "dolarCasa" elige
  // con cuál cotización convertir.
  const [dolares, setDolares] = useState<DolarRate[]>([]);
  const [convertOn, setConvertOn] = useState(false);
  const [dolarCasa, setDolarCasa] = useState("tarjeta");

  useEffect(() => {
    getDolarRates()
      .then(setDolares)
      .catch(() => {
        /* si falla, el botón de conversión simplemente no hace nada */
      });
  }, []);

  const selectedRate = dolares.find((d) => d.casa === dolarCasa);
  const usdToArs = convertOn ? selectedRate?.venta : undefined;

  useEffect(() => {
    if (mode === "home") {
      loadedPageRef.current = 1;
      load(() => home(1));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  async function load(fetcher: () => Promise<CompareResponse>) {
    setLoading(true);
    setError(null);
    try {
      setResult(await fetcher());
    } catch (err) {
      setError((err as Error).message);
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  function handleSearch(e: FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setMode("search");
    setTab("comparar");
    load(() => compare(query.trim()));
  }

  function backToHome() {
    setMode("home");
    setTab("comparar");
    setQuery("");
  }

  // Guardas SINCRÓNICAS (ref, no state) contra pedir la misma página dos
  // veces: el sentinel puede disparar el IntersectionObserver más de una
  // vez seguida (se reconecta cada vez que cambia result.page, y si el
  // sentinel sigue en pantalla dispara de nuevo) antes de que React
  // llegue a procesar el setLoadingMore(true) anterior -- con solo el
  // state como guarda, esa segunda llamada pasaba el chequeo igual y
  // pedía la MISMA página de nuevo, duplicando las tarjetas. loadingMoreRef
  // se marca en el mismo tick (no espera al render) y loadedPageRef evita
  // reprocesar una página que ya se agregó aunque la llamada se dispare
  // después de que la anterior ya terminó.
  const loadingMoreRef = useRef(false);
  const loadedPageRef = useRef<number | null>(1);

  async function handleLoadMore() {
    if (!result || !result.has_more || loading) return;
    const nextPage = (result.page ?? 1) + 1;
    if (loadingMoreRef.current || loadedPageRef.current === nextPage) return;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    setError(null);
    try {
      const next = await home(nextPage);
      loadedPageRef.current = nextPage;
      setResult((prev) =>
        prev
          ? {
            ...next,
            matched: [...prev.matched, ...next.matched],
            steam_only: [...prev.steam_only, ...next.steam_only],
            pc_stores: [...prev.pc_stores, ...next.pc_stores],
          }
          : next,
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      loadingMoreRef.current = false;
      setLoadingMore(false);
    }
  }

  // Scroll infinito: en vez de un botón "cargar más", un sentinel
  // invisible al final de la grilla dispara la carga de la próxima
  // página apenas entra en viewport (con 400px de margen, para que
  // empiece a cargar un poco antes de que el usuario llegue al fondo).
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (mode !== "home") return;
    const el = sentinelRef.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) handleLoadMore();
      },
      { rootMargin: "400px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, result?.page, result?.has_more]);

  function handleWatch(target: WatchTarget) {
    const items = addLocalWatchlistItem(target);
    setWatchedIds(new Set(items.map((i) => i.id)));
  }

  return (
    <div className="min-h-screen px-6 py-8 md:px-10 lg:px-14">
      <header className="mb-6 flex flex-wrap items-center gap-4 border-b border-base-300 pb-4">
        <h1
          onClick={backToHome}
          className="cursor-pointer shrink-0 text-xl font-bold uppercase tracking-wide text-primary"
        >
          DESCUENTITO
        </h1>

        <form onSubmit={handleSearch} className="w-64 max-w-xs">
          <label className="input input-bordered flex items-center gap-2 rounded-full bg-base-200 shadow-sm">
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
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Buscá un juego..."
              className="w-full grow bg-transparent outline-none"
            />
          </label>
        </form>
      </header>

      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          {tab === "comparar" && (
            <div className="flex border-2 border-base-content/40">
              <button
                type="button"
                onClick={() => setStoreFilter("all")}
                className={`px-3 py-1.5 text-sm font-semibold ${storeFilter === "all"
                  ? "bg-base-content text-base-100"
                  : "bg-transparent text-base-content/60"
                  }`}
              >
                todas
              </button>
              {ALL_STORES.map((key) => {
                const meta = STORE_META[key];
                const active = storeFilter === key;
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setStoreFilter(key)}
                    className="border-l-2 border-base-content/40 px-3 py-1.5 text-sm font-semibold"
                    style={{
                      backgroundColor: active ? meta.bg : "transparent",
                      color: active ? meta.fg : "inherit",
                      opacity: active ? 1 : 0.6,
                    }}
                  >
                    {meta.label}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => setTab(tab === "seguimiento" ? "comparar" : "seguimiento")}
          >
            {tab === "seguimiento" ? "← volver a ofertas" : "★ mis seguidos"}
          </button>
          <CafecitoButton />
          <button
            className="btn btn-ghost btn-circle btn-sm"
            onClick={toggleTheme}
            aria-label="Cambiar tema claro/oscuro"
            title="Cambiar tema claro/oscuro"
          >
            {theme === "descuentito" ? (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="h-5 w-5"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z"
                />
              </svg>
            ) : (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="h-5 w-5"
              >
                <circle cx="12" cy="12" r="4" />
                <path
                  strokeLinecap="round"
                  d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"
                />
              </svg>
            )}
          </button>
        </div>
      </div>

      {!disclaimerDismissed && (
        <div className="alert mb-6 items-start border border-warning/30 bg-warning/10 text-sm text-base-content">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className="h-5 w-5 shrink-0 text-warning"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 9v4m0 4h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"
            />
          </svg>
          <span>
            Los precios que ves son de referencia y pueden no incluir impuestos
            ni percepciones (por ejemplo, compras con tarjeta en el exterior).
            No son necesariamente el precio final que vas a pagar — confirmalo
            siempre en la tienda antes de comprar.
          </span>
          <button
            className="btn btn-ghost btn-circle btn-xs"
            onClick={dismissDisclaimer}
            aria-label="Cerrar aviso"
            title="Cerrar"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              className="h-4 w-4"
            >
              <path strokeLinecap="round" d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </div>
      )}

      {dolares.length > 0 && (
        <div className="mb-6 flex flex-wrap items-center gap-2 text-sm">
          <button
            className={`btn btn-sm ${convertOn ? "btn-primary" : "btn-outline"}`}
            onClick={() => setConvertOn((v) => !v)}
          >
            {convertOn ? "USD → ARS activado" : "Convertir USD a ARS"}
          </button>
          {convertOn && (
            <>
              <select
                className="select select-bordered select-sm"
                value={dolarCasa}
                onChange={(e) => setDolarCasa(e.target.value)}
              >
                {dolares.map((d) => (
                  <option key={d.casa} value={d.casa}>
                    Dólar {d.nombre}
                  </option>
                ))}
              </select>
              {selectedRate && (
                <span className="text-base-content/50">
                  ${selectedRate.venta.toLocaleString("es-AR")} por USD
                </span>
              )}
            </>
          )}
        </div>
      )}

      {tab === "comparar" && (
        <>
          {error && <p className="mb-4 text-sm text-error">{error}</p>}

          {loading ? (
            // Búsqueda nueva o primera carga de inicio: todavía no hay
            // nada que mostrar debajo, así que la grilla fantasma ocupa
            // todo el lugar de los resultados.
            <SkeletonGrid />
          ) : (
            result && (
              <CompareTable
                data={result}
                onWatch={handleWatch}
                watchedIds={watchedIds}
                usdToArs={usdToArs}
                storeFilter={storeFilter}
                searchQuery={mode === "search" ? result.query : undefined}
              />
            )
          )}

          {mode === "home" && result && !loading && (
            <div className="mt-6">
              {result.has_more ? (
                loadingMore ? (
                  // Página siguiente del scroll infinito: se suma una
                  // tanda más chica de tarjetas fantasma debajo de lo
                  // que ya está cargado, en vez de tapar todo.
                  <SkeletonGrid count={4} className="mt-4" />
                ) : (
                  <div className="flex justify-center">
                    <span className="text-sm text-base-content/50">
                      mostrando {result.matched.length + result.steam_only.length} de{" "}
                      {result.total}
                    </span>
                  </div>
                )
              ) : (
                <div className="flex justify-center">
                  <span className="text-sm text-base-content/50">
                    eso es todo lo que hay en oferta ahora ({result.total})
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Sentinel invisible: cuando entra en pantalla, dispara la carga de la próxima página. */}
          {mode === "home" && result?.has_more && (
            <div ref={sentinelRef} className="h-1 w-full" />
          )}
        </>
      )}

      {tab === "seguimiento" && <WatchlistPanel usdToArs={usdToArs} />}
    </div>
  );
}

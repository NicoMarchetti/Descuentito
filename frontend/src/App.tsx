import { FormEvent, useEffect, useState } from "react";
import { compare, getDolarRates, home } from "./lib/api";
import { addLocalWatchlistItem, getLocalWatchlist } from "./lib/watchlist";
import type { CompareResponse, DolarRate } from "./lib/types";
import { CompareTable, type WatchTarget } from "./components/CompareTable";
import { WatchlistPanel } from "./components/WatchlistPanel";

type Tab = "comparar" | "seguimiento";
type Mode = "home" | "search";

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
    if (mode === "home") load(() => home(1));
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
    load(() => compare(query.trim()));
  }

  function backToHome() {
    setMode("home");
    setQuery("");
  }

  async function handleLoadMore() {
    if (!result || !result.has_more) return;
    const nextPage = (result.page ?? 1) + 1;
    setLoadingMore(true);
    setError(null);
    try {
      const next = await home(nextPage);
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
      setLoadingMore(false);
    }
  }

  function handleWatch(target: WatchTarget) {
    const items = addLocalWatchlistItem(target);
    setWatchedIds(new Set(items.map((i) => i.id)));
  }

  return (
    <div className="min-h-screen px-6 py-8 md:px-10 lg:px-14">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-4 border-b border-base-300 pb-4">
        <h1
          onClick={backToHome}
          className="cursor-pointer text-xl font-bold uppercase tracking-wide text-primary"
        >
          DESCUENTITO
        </h1>
        <div className="flex items-center gap-3">
          <div role="tablist" className="tabs tabs-boxed bg-base-200">
            <a
              role="tab"
              className={`tab ${tab === "comparar" ? "tab-active" : ""}`}
              onClick={() => setTab("comparar")}
            >
              comparar
            </a>
            <a
              role="tab"
              className={`tab ${tab === "seguimiento" ? "tab-active" : ""}`}
              onClick={() => setTab("seguimiento")}
            >
              seguimiento
            </a>
          </div>
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
      </header>

      <div className="alert mb-6 border border-warning/30 bg-warning/10 text-sm text-base-content">
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
      </div>

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
          <form onSubmit={handleSearch} className="mb-8">
            <label className="input input-bordered flex max-w-lg items-center gap-2 rounded-full bg-base-200 shadow-sm">
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
                autoFocus
                className="w-full grow bg-transparent outline-none"
              />
            </label>
          </form>

          {loading && (
            <p className="mb-4 text-sm text-base-content/50">cargando...</p>
          )}
          {error && <p className="mb-4 text-sm text-error">{error}</p>}
          {result && (
            <CompareTable
              data={result}
              onWatch={handleWatch}
              watchedIds={watchedIds}
              usdToArs={usdToArs}
            />
          )}

          {mode === "home" && result && !loading && (
            <div className="mt-6 flex justify-center">
              {result.has_more ? (
                <button
                  className="btn btn-outline btn-primary btn-sm"
                  onClick={handleLoadMore}
                  disabled={loadingMore}
                >
                  {loadingMore
                    ? "cargando..."
                    : `cargar más (${result.matched.length + result.steam_only.length} de ${result.total})`}
                </button>
              ) : (
                <span className="text-sm text-base-content/50">
                  eso es todo lo que hay en oferta ahora ({result.total})
                </span>
              )}
            </div>
          )}
        </>
      )}

      {tab === "seguimiento" && <WatchlistPanel usdToArs={usdToArs} />}
    </div>
  );
}

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { compare, getDolarRates, home, type HomeFilters, type HomeSort } from "./lib/api";
import { addLocalWatchlistItem, getLocalWatchlist } from "./lib/watchlist";
import type { CompareResponse, DolarRate } from "./lib/types";
import { CompareTable, visibleGameCount, type WatchTarget } from "./components/CompareTable";
import { WatchlistPanel } from "./components/WatchlistPanel";
import { CafecitoButton } from "./components/CafecitoButton";
import { HeroSkeleton, SkeletonGrid } from "./components/SkeletonGrid";
import { FeaturedCarousel, featuredGameNames } from "./components/FeaturedCarousel";
import { FilterSidebar } from "./components/FilterSidebar";
import pageIcon from "../src/assets/icons/icon.png";

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

  // Filtros del sidebar -- van al backend (/api/home filtra ANTES de
  // paginar, ver app.py). "selectedStores" vacío = sin filtro de tienda
  // (todas). Switch no se puede filtrar acá, ver FilterSidebar. (Nota: NO
  // hay filtro de "solo seguidos" acá a propósito -- eso ya es la
  // pestaña "seguimiento" aparte, no hace falta duplicarlo como filtro
  // de la grilla principal.)
  // "switch" se suma a las otras 3, pero es DISTINTO: a esas el backend
  // las filtra ANTES de paginar, contra el pool completo (ver
  // _filter_candidates en app.py). Para Switch no hay forma barata de
  // hacer lo mismo -- no hay API de Nintendo, así que cada juego se
  // chequea contra DekuDeals uno por uno (dekudeals_check), y eso solo
  // se hace para los juegos de la página que se está mostrando, no para
  // los cientos de candidatos del pool entero (hacerlo para todos de
  // una sería lento -- decenas de segundos cada vez que vence la cache
  // -- y le pegaría muchísimo tráfico de scraping a DekuDeals). Entonces
  // "switch" filtra solo lo que YA se cargó en pantalla (ver
  // CompareTable.switchOnly más abajo): no dispara ningún pedido nuevo
  // al backend, y a medida que el scroll infinito trae más páginas, más
  // juegos entran a filtrar.
  type SidebarStore = "steam" | "epic" | "gog" | "switch";
  const [selectedStores, setSelectedStores] = useState<Set<SidebarStore>>(new Set());
  const [minDiscount, setMinDiscount] = useState(0);
  const [priceMinStr, setPriceMinStr] = useState("");
  const [priceMaxStr, setPriceMaxStr] = useState("");

  // Debounce de los inputs de precio: sin esto, cada tecla dispararía un
  // pedido nuevo al backend. 500ms después de la última tecla, recién
  // ahí se actualiza el filtro "de verdad" que dispara la carga.
  const [priceMin, setPriceMin] = useState<number | undefined>(undefined);
  const [priceMax, setPriceMax] = useState<number | undefined>(undefined);
  useEffect(() => {
    const t = setTimeout(() => {
      setPriceMin(priceMinStr ? Number(priceMinStr) : undefined);
      setPriceMax(priceMaxStr ? Number(priceMaxStr) : undefined);
    }, 500);
    return () => clearTimeout(t);
  }, [priceMinStr, priceMaxStr]);

  function clearSidebarFilters() {
    setSelectedStores(new Set());
    setMinDiscount(0);
    setPriceMinStr("");
    setPriceMaxStr("");
  }

  function toggleSidebarStore(store: SidebarStore) {
    setSelectedStores((prev) => {
      const next = new Set(prev);
      if (next.has(store)) next.delete(store);
      else next.add(store);
      return next;
    });
  }

  // "switch" NO se manda al backend -- ahí no existe como filtro (ver el
  // comentario arriba de selectedStores). Se saca acá antes de armar
  // "filters" (lo que sí viaja a /api/home), así que tocar el checkbox
  // de Switch no dispara ningún pedido nuevo: solo cambia qué tarjetas
  // ya cargadas se muestran (ver switchOnly más abajo).
  const backendStores = Array.from(selectedStores).filter(
    (s): s is "steam" | "epic" | "gog" => s !== "switch",
  );
  const switchOnly = selectedStores.has("switch");

  const filters: HomeFilters = {
    stores: backendStores.length > 0 ? backendStores : undefined,
    minDiscount: minDiscount || undefined,
    priceMin,
    priceMax,
  };

  // Orden de "todas las ofertas" (la grilla paginada) -- SIN selector:
  // el usuario pidió sacarlo, los juegos vienen tal cual los manda Steam
  // (categoría "specials"), sin ningún criterio propio por encima. Ver
  // home_candidates en el backend para el detalle de qué hace
  // sort="relevancia".
  const sort: HomeSort = "relevancia";

  // Sección curada de la home ("ofertas destacadas"): los MÁS VENDIDOS
  // de Steam ahora mismo (el ranking real de Valve, no una aproximación
  // nuestra) que ADEMÁS tienen descuento activo -- antes el criterio era
  // "precio de lista más alto", que terminaba sacando juegos indie caros
  // pero poco conocidos (ver home_candidates en el backend). Esto es más
  // defendible: "destacado" = realmente popular AHORA, no simplemente
  // caro. Se pide UNA vez al entrar a la home (no depende de "sort" ni
  // se pagina), en paralelo con la carga de la grilla principal.
  const [destacados, setDestacados] = useState<CompareResponse | null>(null);
  const destacadosRequestIdRef = useRef(0);

  useEffect(() => {
    if (mode !== "home") return;
    const requestId = ++destacadosRequestIdRef.current;
    // page_size=6: coincide con MIN_DESTACADOS del backend (ver
    // home_candidates en app.py) y con las 3 páginas de 2 del carrusel
    // (ver FeaturedCarousel) -- entra exacto, sin página final a medio
    // llenar.
    home(1, "mas_vendidas", 6, true)
      .then((data) => {
        if (requestId === destacadosRequestIdRef.current) setDestacados(data);
      })
      .catch(() => {
        if (requestId === destacadosRequestIdRef.current) setDestacados(null);
      });
  }, [mode]);

  // Juegos que ya salen en "Destacados" -- se excluyen de la grilla
  // general de abajo (ver CompareTable.excludeNames) para no repetir la
  // misma tarjeta dos veces en la misma pantalla.
  const destacadosNames = useMemo(
    () => (destacados ? featuredGameNames(destacados) : undefined),
    [destacados],
  );

  // Cuántas tarjetas se ven REALMENTE en la grilla de abajo -- antes los
  // contadores ("N ofertas encontradas", "mostrando X de Y") usaban
  // result.total / result.matched.length directo, que es el total del
  // backend ANTES de excluir los que ya salen en Destacados. Filtrando
  // por una sola tienda eso se notaba mucho: decía "9 ofertas
  // encontradas" pero la grilla mostraba 3 tarjetas, porque las otras 6
  // ya estaban arriba en Destacados. Con esto el número que se ve
  // siempre coincide con la cantidad real de tarjetas en pantalla.
  const visibleCount = result
    ? visibleGameCount(result, {
      excludeNames: mode === "home" ? destacadosNames : undefined,
      switchOnly: mode === "home" && switchOnly,
    })
    : undefined;

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

  // Serializado para poder comparar los filtros por VALOR en el array de
  // dependencias (un Set/objeto nuevo en cada render dispararía el efecto
  // sin que el contenido haya cambiado de verdad).
  const filtersKey = JSON.stringify(filters);

  useEffect(() => {
    if (mode === "home") {
      loadedPageRef.current = 1;
      load(() => home(1, sort, undefined, undefined, filters));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, sort, filtersKey]);

  // Guarda contra respuestas "viejas" pisando una más nueva: si dos
  // pedidos de /api/home quedan en vuelo al mismo tiempo (por ejemplo, en
  // desarrollo React.StrictMode dispara el efecto de carga inicial DOS
  // veces seguidas) y el que salió primero tarda más en volver, sin esto
  // su respuesta llega después y pisa el resultado ya mostrado -- eso es
  // justo el bug de "los juegos cargan y después se van/cambian solos".
  // Con este contador, solo se aplica la respuesta del ÚLTIMO pedido
  // disparado; cualquier respuesta más vieja que llegue tarde se ignora.
  const requestIdRef = useRef(0);

  async function load(fetcher: () => Promise<CompareResponse>) {
    const requestId = ++requestIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const data = await fetcher();
      if (requestId !== requestIdRef.current) return; // ya hay un pedido más nuevo en curso
      setResult(data);
    } catch (err) {
      if (requestId !== requestIdRef.current) return;
      setError((err as Error).message);
      setResult(null);
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
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
      const next = await home(nextPage, sort, undefined, undefined, filters);
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
    // "sort"/"filtersKey" entran en las deps para que el observer se
    // reconecte con un handleLoadMore fresco (que cierra sobre esos
    // valores nuevos) apenas cambian -- si no, mientras result.page no
    // cambie de valor (puede volver a ser 1 con el filtro nuevo), se
    // queda con el handleLoadMore viejo y pediría la página siguiente
    // con el sort/filtro anterior.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, sort, filtersKey, result?.page, result?.has_more]);

  function handleWatch(target: WatchTarget) {
    const items = addLocalWatchlistItem(target);
    setWatchedIds(new Set(items.map((i) => i.id)));
  }

  return (
    <div className="min-h-screen px-6 py-8 md:px-10 lg:px-14">
      <header className="mb-6 flex flex-wrap items-center gap-4 border-b border-base-300 pb-4">

        <div onClick={backToHome} className="flex cursor-pointer items-center gap-2 shrink-0">
          <img src={pageIcon} alt="Descuentito" className="w-12" />
          <div className="flex flex-col">
            <h1 className="text-xl font-bold uppercase tracking-wide text-primary">
              DESCUENTITO
            </h1>
            <span className="font-mono text-[0.65rem] uppercase tracking-wide text-base-content/40">
              Jugá al mejor precio
            </span>
          </div>
        </div>

        {/* El buscador se movió al sidebar (ver FilterSidebar) -- queda
            ahí junto al resto de los filtros en vez de acá. */}
        <button
          type="button"
          className="btn btn-outline btn-sm ml-auto gap-1.5"
          onClick={() => setTab(tab === "seguimiento" ? "comparar" : "seguimiento")}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill={tab === "seguimiento" ? "currentColor" : "none"}
            stroke="currentColor"
            strokeWidth="2"
            className="h-4 w-4"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 21s-6.7-4.35-9.33-8.2C.86 9.94 1.6 6.4 4.6 5.03c2.1-.96 4.3-.2 5.6 1.57a.97.97 0 0 0 1.6 0c1.3-1.77 3.5-2.53 5.6-1.57 3 1.37 3.74 4.9 1.93 7.77C18.7 16.65 12 21 12 21Z"
            />
          </svg>
          {tab === "seguimiento" ? "volver a ofertas" : `favoritos ${watchedIds.size}`}
        </button>
        <CafecitoButton />
      </header>

      {/* "En mi lista" y el selector de orden ya no están acá -- "En mi
          lista" se sacó del todo (ya existe la pestaña "seguidos") y el
          orden se movió junto al contador de "N ofertas encontradas".
          Cafecito se movió al pie de página. Lo único que queda acá es
          el tema claro/oscuro. */}
      <div className="mb-6 flex items-center justify-end gap-3">
        <div className="flex items-center gap-3">
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

      {tab === "comparar" && (
        <>
          {error && <p className="mb-4 text-sm text-error">{error}</p>}

          {/* "Ofertas destacadas": carrusel con un puñado de juegos
              grandes con descuento activo -- antes era un solo banner
              fijo ("oferta de la semana"), ahora son varias ofertas que
              rotan. Solo en la home, nunca en una búsqueda. Mientras
              todavía no llegó la respuesta, un fantasma del mismo alto
              para que no salte el layout; si llegó vacía, no se muestra
              nada (no todas las corridas tienen juegos grandes en oferta). */}
          {mode === "home" && destacados === null && <HeroSkeleton />}
          {mode === "home" && !!destacados && destacados.matched.length + destacados.steam_only.length > 0 && (
            <FeaturedCarousel
              data={destacados}
              onWatch={handleWatch}
              watchedIds={watchedIds}
              usdToArs={usdToArs}
            />
          )}

          {mode === "home" && (
            <div className="mb-4">
              <span className="font-mono text-xs uppercase tracking-wide text-primary">
                — Precios que bajaron hoy
              </span>
              <h2 className="text-lg font-bold">Ofertas para jugar más, gastando menos</h2>
              <p className="text-sm text-base-content/50">
                {result?.total !== undefined
                  ? `Comparamos ${result.total.toLocaleString("es-AR")} precios en tiendas oficiales. Elegí, comparás y guardá tus favoritos.`
                  : "Comparamos precios en tiendas oficiales. Elegí, comparás y guardá tus favoritos."}
              </p>
            </div>
          )}

          <div className="flex flex-col gap-6 lg:flex-row">
            <FilterSidebar
              query={query}
              onQueryChange={setQuery}
              onSearch={handleSearch}
              // Los filtros de tienda/descuento/precio solo valen contra
              // /api/home (ver app.py) -- en modo búsqueda (/api/compare)
              // no tienen ningún efecto, así que se ocultan en vez de
              // mostrar controles que no hacen nada.
              hideHomeFilters={mode !== "home"}
              facets={result?.facets}
              // "matched" son justo los juegos YA cargados que SÍ están en
              // Switch (así los separa route_home) -- ya vienen sin
              // repetidos (el pool se dedupea por nombre en
              // home_candidates), así que alcanza con el largo del array.
              switchCount={mode === "home" ? result?.matched.length : undefined}
              selectedStores={selectedStores}
              onToggleStore={toggleSidebarStore}
              minDiscount={minDiscount}
              onSetMinDiscount={setMinDiscount}
              priceMin={priceMinStr}
              priceMax={priceMaxStr}
              onPriceMinChange={setPriceMinStr}
              onPriceMaxChange={setPriceMaxStr}
              dolares={dolares}
              convertOn={convertOn}
              onToggleConvert={() => setConvertOn((v) => !v)}
              dolarCasa={dolarCasa}
              onChangeDolarCasa={setDolarCasa}
              onClear={clearSidebarFilters}
            />

            <div className="min-w-0 flex-1">
              {!loading && result && mode === "home" && (
                <div className="mb-3 font-mono text-xs text-base-content/50">
                  {(visibleCount ?? 0).toLocaleString("es-AR")} ofertas encontradas
                </div>
              )}

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
                    searchQuery={mode === "search" ? result.query : undefined}
                    excludeNames={mode === "home" ? destacadosNames : undefined}
                    switchOnly={mode === "home" && switchOnly}
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
                          mostrando {visibleCount ?? 0} de {result.total}
                        </span>
                      </div>
                    )
                  ) : (
                    <div className="flex justify-center">
                      <span className="text-sm text-base-content/50">
                        eso es todo lo que hay en oferta ahora ({visibleCount ?? 0})
                      </span>
                    </div>
                  )}
                </div>
              )}

              {/* Sentinel invisible: cuando entra en pantalla, dispara la carga de la próxima página. */}
              {mode === "home" && result?.has_more && (
                <div ref={sentinelRef} className="h-1 w-full" />
              )}
            </div>
          </div>
        </>
      )}

      {tab === "seguimiento" && <WatchlistPanel usdToArs={usdToArs} />}

      <footer className="mt-12 flex justify-center border-t border-base-300 pt-6">

      </footer>
    </div>
  );
}

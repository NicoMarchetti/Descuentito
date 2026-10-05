interface Props {
  count?: number;
  className?: string;
}

// Tarjetas "fantasma" con shimmer -- se usan mientras se busca o se carga
// la próxima página, para que la espera se sienta viva en vez de un
// simple texto "cargando...". Imitan la forma de una tarjeta real de
// OfferCardGrid (banner ancho arriba, nombre, un renglón de precio), así
// la grilla no "salta" cuando llegan los datos de verdad.
export function SkeletonGrid({ count = 8, className }: Props) {
  return (
    <div
      className={`grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 ${className ?? ""}`}
    >
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="overflow-hidden border border-base-300 bg-base-200 shadow-sm"
        >
          <div className="aspect-[16/9] w-full skeleton-shimmer bg-base-300" />
          <div className="flex flex-col gap-2 p-3">
            <div className="h-3 w-1/4 skeleton-shimmer bg-base-300" />
            <div className="h-4 w-4/5 skeleton-shimmer bg-base-300" />
            <div className="mt-1 flex items-end justify-between gap-2">
              <div className="h-5 w-16 skeleton-shimmer bg-base-300" />
              <div className="h-6 w-16 skeleton-shimmer bg-base-300" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// Fantasma de "Destacados" -- misma forma que el real (ver
// FeaturedCarousel: 2 tarjetas en fila, cada una imagen 16:9 + panel de
// info debajo) para que no salte el layout cuando llegan los datos.
export function HeroSkeleton() {
  return (
    <div className="mb-10">
      <div className="mb-3 h-3 w-24 skeleton-shimmer bg-base-300" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="overflow-hidden border border-base-300 bg-base-200">
            <div className="aspect-[16/9] w-full skeleton-shimmer bg-base-300" />
            <div className="flex flex-col gap-2 p-3">
              <div className="h-3 w-1/3 skeleton-shimmer bg-base-300" />
              <div className="h-4 w-2/3 skeleton-shimmer bg-base-300" />
              <div className="h-5 w-1/3 skeleton-shimmer bg-base-300" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

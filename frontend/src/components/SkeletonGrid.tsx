interface Props {
  count?: number;
  className?: string;
}

// Tarjetas "fantasma" con shimmer -- se usan mientras se busca o se
// carga la próxima página, para que la espera se sienta viva en vez de
// un simple texto "cargando...". Tienen el mismo tamaño/forma que una
// tarjeta real de OfferGrid, así la grilla no "salta" cuando llegan los
// datos de verdad.
export function SkeletonGrid({ count = 8, className }: Props) {
  return (
    <div
      className={`grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 ${className ?? ""}`}
    >
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="overflow-hidden border border-base-300 bg-base-200 shadow-sm"
        >
          <div className="h-5 w-full skeleton-shimmer bg-base-300" />
          <div className="h-[90px] w-full bg-base-100 p-2">
            <div className="h-full w-full skeleton-shimmer bg-base-300" />
          </div>
          <div className="flex flex-col gap-2 p-3">
            <div className="h-4 w-4/5 skeleton-shimmer bg-base-300" />
            <div className="h-4 w-2/5 skeleton-shimmer bg-base-300" />
            <div className="mt-1 flex gap-2">
              <div className="h-6 w-16 skeleton-shimmer bg-base-300" />
              <div className="h-6 w-16 skeleton-shimmer bg-base-300" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

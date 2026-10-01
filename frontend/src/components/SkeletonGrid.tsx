interface Props {
  count?: number;
  className?: string;
}

// Tarjetas "fantasma" con shimmer -- se usan mientras se busca o se carga
// la próxima página, para que la espera se sienta viva en vez de un
// simple texto "cargando...". Imitan la forma de una tarjeta real de
// GameGroupGrid (banner ancho arriba, nombre + botón, renglones de tienda
// abajo), así la grilla no "salta" cuando llegan los datos de verdad.
export function SkeletonGrid({ count = 8, className }: Props) {
  return (
    <div
      className={`grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 ${className ?? ""}`}
    >
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="overflow-hidden border border-base-300 bg-base-200 shadow-sm"
        >
          <div className="aspect-[16/9] w-full skeleton-shimmer bg-base-300" />
          <div className="flex items-center justify-between gap-2 p-3">
            <div className="h-4 w-3/5 skeleton-shimmer bg-base-300" />
            <div className="h-5 w-14 skeleton-shimmer bg-base-300" />
          </div>
          <div className="divide-y divide-base-300 border-t border-base-300">
            {Array.from({ length: 2 }).map((_, j) => (
              <div key={j} className="flex items-center gap-2 px-3 py-2">
                <div className="h-6 w-6 shrink-0 rounded-full skeleton-shimmer bg-base-300" />
                <div className="h-4 w-20 skeleton-shimmer bg-base-300" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

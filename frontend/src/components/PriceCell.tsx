interface PriceCellProps {
  storeLabel: string;
  available: boolean;
  currency?: string;
  finalPrice?: number;
  initialPrice?: number;
  discountPercent?: number;
  isFree?: boolean;
  unavailableReason?: string;
  /** Si viene y currency es "USD", se muestra convertido a ARS (con el original en USD chiquito al lado). */
  usdToArs?: number;
  /** Resalta esta celda como el precio más barato de la fila. */
  isCheapest?: boolean;
}

function formatPrice(value: number, currency: string): string {
  try {
    return new Intl.NumberFormat("es-AR", {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(value);
  } catch {
    return `${currency} ${value.toFixed(2)}`;
  }
}

export function PriceCell({
  storeLabel,
  available,
  currency,
  finalPrice,
  initialPrice,
  discountPercent,
  isFree,
  unavailableReason,
  usdToArs,
  isCheapest,
}: PriceCellProps) {
  const convert = !!usdToArs && currency === "USD";
  // Algunas tiendas marcan gratis con discount_percent:100 y final_price:0
  // en vez de un flag "is_free" -- lo tratamos igual.
  const free = isFree || (available && finalPrice === 0);

  return (
    <div className="flex flex-col gap-0.5 font-mono text-sm">
      <span className="text-[0.65rem] uppercase tracking-wide text-base-content/50">
        {storeLabel}
      </span>

      {!available && (
        <span className="text-base-content/40">
          {unavailableReason ?? "no disponible"}
        </span>
      )}

      {available && free && (
        <span className="font-bold text-accent">GRATIS</span>
      )}

      {available && !free && currency && finalPrice !== undefined && (
        <div className="flex flex-wrap items-center gap-1">
          <span
            className={
              isCheapest
                ? "font-bold text-accent"
                : discountPercent
                  ? "font-medium text-success"
                  : ""
            }
          >
            {convert
              ? formatPrice(finalPrice * usdToArs!, "ARS")
              : formatPrice(finalPrice, currency)}
          </span>
          {!!discountPercent &&
            initialPrice !== undefined &&
            initialPrice !== finalPrice && (
              <span className="text-xs text-base-content/40 line-through">
                {convert
                  ? formatPrice(initialPrice * usdToArs!, "ARS")
                  : formatPrice(initialPrice, currency)}
              </span>
            )}
          {!!discountPercent && (
            <span className="badge badge-success badge-sm">
              -{discountPercent}%
            </span>
          )}
          {convert && (
            <span className="text-[0.6rem] text-base-content/40">
              ({formatPrice(finalPrice, "USD")})
            </span>
          )}
        </div>
      )}
    </div>
  );
}

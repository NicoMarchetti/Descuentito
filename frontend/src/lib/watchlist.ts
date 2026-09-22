import type { WatchlistItem } from "./types";

const KEY = "descuentito:watchlist";

export function getLocalWatchlist(): WatchlistItem[] {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as WatchlistItem[]) : [];
  } catch {
    return [];
  }
}

function save(items: WatchlistItem[]) {
  localStorage.setItem(KEY, JSON.stringify(items));
}

export function addLocalWatchlistItem(
  target:
    | { kind: "steam"; appid: number; name: string }
    | { kind: "nintendo"; slug: string; name: string },
): WatchlistItem[] {
  const id =
    target.kind === "steam" ? `steam:${target.appid}` : `nintendo:${target.slug}`;
  const items = getLocalWatchlist();
  if (items.some((i) => i.id === id)) return items;

  const entry: WatchlistItem =
    target.kind === "steam"
      ? { id, kind: "steam", appid: target.appid, name: target.name }
      : { id, kind: "nintendo", slug: target.slug, name: target.name };

  const next = [...items, entry];
  save(next);
  return next;
}

export function removeLocalWatchlistItem(id: string): WatchlistItem[] {
  const next = getLocalWatchlist().filter((i) => i.id !== id);
  save(next);
  return next;
}

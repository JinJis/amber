"use client";

// A distinguished @관심종목 chip for the composer: shows the group and, on hover/focus, a preview
// popover listing the member companies with their logos — so the user SEES which companies the
// @handle will pull into the turn (the agent expands it server-side to those tickers).

import { TickerLogo } from "./TickerLogo";
import type { Watchlist } from "./Watchlists";

export function MentionChip({ group }: { group: Watchlist }) {
  const items = group.items ?? [];
  return (
    <span className="mention-chip" tabIndex={0}>
      <span className="mc-at" aria-hidden>@</span>{group.name}
      <span className="mc-count mono">{group.count}</span>
      <span className="mc-pop" role="tooltip">
        <span className="mc-pop-h mono">@{group.name} · {group.count}종목</span>
        <span className="mc-pop-list">
          {items.slice(0, 12).map((it) => (
            <span key={it.id} className="mc-pop-row">
              <TickerLogo market={it.market} ticker={it.ticker} name={it.name} size={18} />
              <span className="mc-pop-name">{it.name || it.ticker}</span>
              <span className="mc-pop-tk mono">{it.ticker}</span>
            </span>
          ))}
        </span>
        {items.length === 0 && <span className="mc-pop-empty">담긴 종목이 없어요</span>}
        {group.count > 12 && <span className="mc-pop-more mono">외 {group.count - 12}종목</span>}
      </span>
    </span>
  );
}

// IMP-8 — DeskHome's three states (DK-2 accept): nudge with quick-add, sourced feed, and the
// IMP-3 retryable error state. fetch is stubbed; no network.
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DeskHome from "../components/DeskHome";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function stubFeed(body: unknown, ok = true) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok, json: async () => body })));
}

describe("DeskHome", () => {
  it("renders sourced suggestion cards and pre-fills the composer on tap", async () => {
    const onPick = vi.fn();
    stubFeed({ cards: [{ kind: "price_move", question: "AAPL 오늘 가격 흐름 보여줘",
      hook: "AAPL −3.2%", citations: [{ source: "Yahoo Finance", as_of: "2026-07-03", freshness: "fresh" }] }] });
    render(<DeskHome onPick={onPick} />);
    const card = await screen.findByText(/AAPL 오늘 가격 흐름/);
    expect(screen.getByText("Yahoo Finance")).toBeInTheDocument();  // provenance on the card
    card.closest("button")!.click();
    expect(onPick).toHaveBeenCalledWith("AAPL 오늘 가격 흐름 보여줘");  // pre-fill, never auto-send
  });

  it("SH-5: composes the top sourced cards into one briefing share artifact", async () => {
    const onShareBriefing = vi.fn();
    stubFeed({ generated_at: "2026-07-05T08:00:00", cards: [
      { kind: "price_move", question: "q1", hook: "AAPL −3.2%",
        citations: [{ source: "Yahoo Finance", as_of: "2026-07-05" }] },
      { kind: "watchlist_nudge", question: "", hook: "관심그룹을 만들면…" },  // skipped (no source)
      { kind: "filing_new", question: "q2", hook: "TSLA 8-K 접수",
        citations: [{ source: "SEC EDGAR", as_of: "2026-07-05" }] },
    ] });
    render(<DeskHome onPick={() => {}} onShareBriefing={onShareBriefing} />);
    const btn = await screen.findByText(/오늘 브리핑 공유/);
    btn.click();
    expect(onShareBriefing).toHaveBeenCalledTimes(1);
    const a = onShareBriefing.mock.calls[0][0];
    expect(a.kind).toBe("table");
    expect(a.title).toContain("2026-07-05");
    // header row + the two SOURCED cards (the nudge is excluded)
    expect(a.table).toHaveLength(3);
    expect(a.table[1][0]).toBe("AAPL −3.2%");
    expect(a.table[1][1]).toContain("Yahoo Finance");
    expect(a.table.some((r: string[]) => r[0] === "관심그룹을 만들면…")).toBe(false);
  });

  it("leads with the watchlist nudge (preset quick-add) for a user without groups", async () => {
    stubFeed({ cards: [{ kind: "watchlist_nudge", question: "관심그룹 만들기",
      hook: "관심그룹을 만들면 데스크가 채워둡니다", citations: [] }] });
    render(<DeskHome onPick={() => {}} />);
    await screen.findByText(/관심그룹을 만들면/);
    expect(screen.getByText("＋ 반도체")).toBeInTheDocument();       // shared PRESETS quick-add
  });

  it("shows a retryable error state on fetch failure — never an endless skeleton (IMP-3)", async () => {
    stubFeed({}, false);
    render(<DeskHome onPick={() => {}} />);
    await screen.findByRole("alert");
    expect(screen.getByText("다시 시도")).toBeInTheDocument();
  });

  it("renders nothing when the feed is empty (parent capability examples remain)", async () => {
    stubFeed({ cards: [] });
    const { container } = render(<DeskHome onPick={() => {}} />);
    await waitFor(() => expect(container.querySelector(".df-skel")).toBeNull());
    expect(container.firstChild).toBeNull();
  });
});

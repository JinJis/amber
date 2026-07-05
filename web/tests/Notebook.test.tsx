// NB-2/3 — 담기 시트(피커)와 노트 문서: 스냅샷 담기 payload, 왜-메모, 블록 렌더/편집.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NotebookPicker } from "../components/NotebookPicker";
import NotebookView from "../components/NotebookView";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("NotebookPicker (담기 시트)", () => {
  it("최근 노트북 선택 + 왜-메모와 함께 블록 POST", async () => {
    const calls: { url: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (String(url) === "/api/notebooks" && !init?.method) {
        return { ok: true, json: async () => ({ notebooks: [{ id: "nb1", title: "반도체", blocks: 3 }] }) };
      }
      return { ok: true, json: async () => ({ id: "blk1" }) };
    }));
    const onClose = vi.fn();
    render(<NotebookPicker onClose={onClose}
      pin={{ kind: "pin_ledger", title: "수치 391.0B", payload: { raw: "391.0B", citation_idx: 1 } }} />);
    await screen.findByText("반도체");
    fireEvent.change(screen.getByPlaceholderText(/왜 담나요/), { target: { value: "매출 근거" } });
    fireEvent.click(screen.getByText("담기"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const post = calls.find((c) => c.url === "/api/notebooks/nb1/blocks");
    expect(post?.body).toEqual({ kind: "pin_ledger", payload: { raw: "391.0B", citation_idx: 1 }, note: "매출 근거" });
  });

  it("새 노트북 경로: 생성 후 그 노트북에 담는다", async () => {
    const calls: { url: string; method?: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), method: init?.method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (String(url) === "/api/notebooks" && !init?.method) return { ok: true, json: async () => ({ notebooks: [] }) };
      if (String(url) === "/api/notebooks" && init?.method === "POST") return { ok: true, json: async () => ({ id: "nb9" }) };
      return { ok: true, json: async () => ({ id: "blk1" }) };
    }));
    render(<NotebookPicker onClose={() => {}}
      pin={{ kind: "pin_artifact", payload: { kind: "table", title: "t" } }} />);
    fireEvent.change(await screen.findByPlaceholderText(/노트북 이름/), { target: { value: "새 리서치" } });
    fireEvent.click(screen.getByText("담기"));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/notebooks/nb9/blocks")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.url === "/api/notebooks")?.body).toEqual({ title: "새 리서치" });
  });
});

describe("NotebookView (노트 문서)", () => {
  function stub(doc: Record<string, unknown>) {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (String(url) === "/api/notebooks") {
        return { ok: true, json: async () => ({ notebooks: [{ id: "nb1", title: "반도체 리서치", blocks: 3 }] }) };
      }
      if (String(url).startsWith("/api/notebooks/nb1")) return { ok: true, json: async () => doc };
      return { ok: false, json: async () => ({}) };
    }));
  }
  const DOC = {
    id: "nb1", title: "반도체 리서치", blocks: 3,
    block_list: [
      { id: "b1", kind: "pin_ledger", position: 10, note: "매출 근거",
        payload: { raw: "391.0B", citation_idx: 1, source: "SEC EDGAR" } },
      { id: "b2", kind: "text", position: 20, payload: { md: "## 내 가설" } },
      { id: "b3", kind: "pin_artifact", position: 30, note: null,
        payload: { kind: "table", title: "PER 표", table: [["a", "b"]], source: "SEC" } },
    ],
  };

  it("블록을 종류별 네이티브 모양으로 렌더 + 왜-메모 표시, 알림 UI 없음", async () => {
    stub(DOC);
    render(<NotebookView />);
    await screen.findByText(/391.0B/);
    expect(screen.getByText(/메모: 매출 근거/)).toBeInTheDocument();
    expect(screen.getByText("내 가설")).toBeInTheDocument();           // md 렌더
    expect(screen.getAllByText(/왜 담았는지 한 줄/).length).toBe(1);      // b3 = 메모 비어 있음
    expect(document.querySelector(".alert, .bell, [class*=alert]")).toBeNull();  // 알림 UI 제거 확인
  });

  it("공유 버튼 → kind=note payload로 콜백 (NB-4)", async () => {
    stub(DOC);
    const onShare = vi.fn();
    render(<NotebookView onShare={onShare} />);
    await screen.findByText(/391.0B/);
    fireEvent.click(screen.getByText("↗ 공유"));
    const arg = onShare.mock.calls[0][0];
    expect(arg.kind).toBe("note");
    expect(arg.title).toBe("반도체 리서치");
    expect(arg.blocks).toHaveLength(3);
  });
});

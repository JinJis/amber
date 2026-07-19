// KebabMenu — 답변 액션(복사·재생성·공유)을 오른쪽 끝 "⋯"로 모은 프리미티브.
// 열림/닫힘·아이템 실행·바깥 클릭 닫힘을 핀.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KebabMenu } from "../components/ui";

afterEach(cleanup);

describe("KebabMenu", () => {
  it("닫힌 상태에선 메뉴 없음; ⋯ 클릭 → 아이템 노출, 아이템 클릭 → onClick + 닫힘", () => {
    const copy = vi.fn(); const share = vi.fn();
    render(<KebabMenu label="답변 액션" items={[
      { key: "copy", label: "⧉ 복사", onClick: copy },
      { key: "share", label: "↗ 공유", onClick: share },
    ]} />);
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(screen.getByLabelText("답변 액션"));
    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.getByText("⧉ 복사")).toBeTruthy();
    fireEvent.click(screen.getByText("↗ 공유"));
    expect(share).toHaveBeenCalledTimes(1);
    expect(copy).not.toHaveBeenCalled();
    expect(screen.queryByRole("menu")).toBeNull();               // 실행 후 닫힘
  });

  it("바깥 클릭으로 닫힌다", () => {
    render(<KebabMenu items={[{ key: "a", label: "A", onClick: vi.fn() }]} />);
    fireEvent.click(screen.getByLabelText("더보기"));
    expect(screen.getByRole("menu")).toBeTruthy();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("아이템이 없으면 아무것도 렌더하지 않는다", () => {
    const { container } = render(<KebabMenu items={[]} />);
    expect(container.firstChild).toBeNull();
  });
});

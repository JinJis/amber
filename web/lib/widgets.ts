/**
 * Dashboard widget classification (FE-03).
 *
 * A pinned widget is one of three structural kinds — an artifact card, a source/citation, or a text
 * note — derived from `spec.kind`. Centralized here so the board (BoardCanvas) and the pin picker
 * agree. (Cadence → periodicity / trigger derivation stays in lib/alerts; the artifact's fine-grained
 * display label — 차트 / 표·값 / 내러티브 — stays in WidgetGallery, a different concern.)
 */

export type WidgetKind = "artifact" | "source" | "text";

/** A pinned widget's structural kind (everything that isn't a source/text spec is an artifact card). */
export function widgetKind(spec: any): WidgetKind {
  return spec?.kind === "source" ? "source" : spec?.kind === "text" ? "text" : "artifact";
}

/** Coarse, user-facing label for a widget kind. */
export function widgetKindLabel(kind: WidgetKind): string {
  return kind === "source" ? "출처" : kind === "text" ? "메모" : "자료";
}

// CJK-friendly emphasis for react-markdown.
//
// react-markdown parses CommonMark (via micromark), whose emphasis "flanking" rules don't fire for
// Korean text: a **bold** run that ends in punctuation and is immediately followed by a Korean
// particle with no space — e.g. `**11.2%**입니다` — can't satisfy the right-flanking rule, so the
// closing `**` never closes and the markers render literally. The agent emits a LOT of this.
//
// This tiny remark plugin re-parses the leftover literal `**bold**` / `*italic*` still sitting in
// text nodes and turns them into real `strong` / `emphasis` mdast nodes, bypassing the flanking
// rules entirely. Emphasis that already parsed correctly is a node (not text), so its children hold
// no `*` — only the FAILED markers get fixed; nothing is double-processed.

type MdNode = { type: string; value?: string; children?: MdNode[] };

// `**bold**` (content may hold single `*`/punctuation) OR `*italic*` (no inner `*`). The `\S`
// guards mirror emphasis semantics (content can't start/end with whitespace). `_`/`__` are left
// alone on purpose — underscores show up in identifiers/paths and would false-match.
const EMPH = /\*\*(?=\S)(.+?)(?<=\S)\*\*|\*(?=\S)([^*\n]+?)(?<=\S)\*/g;

function splitEmphasis(value: string): MdNode[] {
  const out: MdNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  EMPH.lastIndex = 0;
  while ((m = EMPH.exec(value)) !== null) {
    if (m.index > last) out.push({ type: "text", value: value.slice(last, m.index) });
    if (m[1] !== undefined) out.push({ type: "strong", children: [{ type: "text", value: m[1] }] });
    else out.push({ type: "emphasis", children: [{ type: "text", value: m[2] }] });
    last = EMPH.lastIndex;
  }
  if (out.length === 0) return [{ type: "text", value }];
  if (last < value.length) out.push({ type: "text", value: value.slice(last) });
  return out;
}

function walk(node: MdNode): void {
  if (!node.children) return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    // only re-scan raw text (inlineCode / code blocks are their own node types → never touched)
    if (child.type === "text" && child.value && child.value.includes("*")) {
      next.push(...splitEmphasis(child.value));
    } else {
      if (child.children) walk(child);
      next.push(child);
    }
  }
  node.children = next;
}

/** remark plugin: fix CJK-adjacent `**bold**` / `*italic*` that CommonMark flanking left literal. */
export function remarkCjkEmphasis() {
  return (tree: MdNode) => walk(tree);
}

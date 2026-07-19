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

type MdNode = { type: string; value?: string; url?: string; children?: MdNode[] };

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

// ── 핵심 (하이라이트 3종 중 '핵심', design template §MARK) ────────────────────────────
// The ONE takeaway the reader should read first — a light-amber block (`.hl-block`), used
// exactly once per answer. The synthesis model wraps it in `==...==` (there is no `==` syntax
// in CommonMark, so it survives untouched as literal text until here). This transformer finds
// the FIRST balanced `==…==` run — even when it straddles [n]/수치 link nodes that the string
// pre-processors already turned into markdown links — and wraps the enclosed inline nodes in a
// `#key` link, which the answer renderer maps to `<mark class="hl-block">`. Only the first run
// is marked (한 답변에 한 번만); an unbalanced/stray `==` is left as-is.

function keyNode(children: MdNode[]): MdNode {
  return { type: "link", url: "#key", children };
}

// Wrap the first `==…==` inside `node`'s inline children. Returns true once it has marked one
// run anywhere in the subtree, so scanning stops (single key mark per tree).
function markKey(node: MdNode): boolean {
  const kids = node.children;
  if (!kids) return false;
  for (let i = 0; i < kids.length; i++) {
    const c = kids[i];
    if (c.type === "text" && c.value && c.value.includes("==")) {
      const open = c.value.indexOf("==");
      const sameClose = c.value.indexOf("==", open + 2);
      if (sameClose !== -1) {
        // open + close in the same text node: split into before / marked / after
        const before = c.value.slice(0, open);
        const mid = c.value.slice(open + 2, sameClose);
        const after = c.value.slice(sameClose + 2);
        const repl: MdNode[] = [];
        if (before) repl.push({ type: "text", value: before });
        if (mid) repl.push(keyNode([{ type: "text", value: mid }]));
        if (after) repl.push({ type: "text", value: after });
        kids.splice(i, 1, ...repl);
        return true;
      }
      // close lives in a later sibling text node — collect the nodes in between
      let closeAt = -1;
      let closePos = -1;
      for (let j = i + 1; j < kids.length; j++) {
        const d = kids[j];
        if (d.type === "text" && d.value && d.value.includes("==")) {
          closeAt = j;
          closePos = d.value.indexOf("==");
          break;
        }
      }
      if (closeAt === -1) return false; // unbalanced → leave the `==` as literal text
      const before = c.value.slice(0, open);
      const openTail = c.value.slice(open + 2);
      const closeNode = kids[closeAt];
      const marked = (closeNode.value || "").slice(0, closePos);
      const closeTail = (closeNode.value || "").slice(closePos + 2);
      const inner: MdNode[] = [];
      if (openTail) inner.push({ type: "text", value: openTail });
      for (let k = i + 1; k < closeAt; k++) inner.push(kids[k]);
      if (marked) inner.push({ type: "text", value: marked });
      const repl: MdNode[] = [];
      if (before) repl.push({ type: "text", value: before });
      repl.push(keyNode(inner));
      if (closeTail) repl.push({ type: "text", value: closeTail });
      kids.splice(i, closeAt - i + 1, ...repl);
      return true;
    }
    // `==` may sit inside a bold/italic run — recurse (but never into the key node we just made)
    if (c.children && markKey(c)) return true;
  }
  return false;
}

/** remark plugin: turn the single `==핵심==` run into a `#key` link → `<mark class="hl-block">`. */
export function remarkKeyMark() {
  return (tree: MdNode) => { markKey(tree); };
}

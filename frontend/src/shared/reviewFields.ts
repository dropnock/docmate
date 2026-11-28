// The fields a reviewer marks on a record (customer QC, the customer QC
// supervisor's review, the digitizing supervisor's outgoing inspection), read
// from the document type's JSON Schema and the record's indexed data. Mirrors
// backend/app/services/review_fields.py — both follow
// backend/tests/fixtures/review_fields_cases.json, so keep them in step.
//
// - A plain top-level property is one field, keyed by its name.
// - A top-level list of objects gives one field per part of each entry the
//   record has ("caveators/1/address" → "Caveators 2 › Address"); a part that
//   is itself a group (an address, a nested list) is one field. A list with
//   no entries is one field for the whole list.
// - A top-level object gives one field per part ("owner/name").
// - Any other list is one field.
// Key segments are escaped as in a JSON Pointer (~ → ~0, / → ~1). A field is
// critical when its top-level property or its own part is x-critical.

type Node = Record<string, unknown>;

export interface ReviewField {
  key: string;
  label: string;
  is_critical: boolean;
  empty: boolean;
}

function isNode(v: unknown): v is Node {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function defsOf(schema: Node): Node {
  return {
    ...(isNode(schema.definitions) ? schema.definitions : {}),
    ...(isNode(schema.$defs) ? schema.$defs : {}),
  };
}

function resolve(node: unknown, defs: Node): unknown {
  if (!isNode(node) || typeof node.$ref !== "string") return node;
  for (const prefix of ["#/definitions/", "#/$defs/"]) {
    if (node.$ref.startsWith(prefix)) {
      const target = defs[node.$ref.slice(prefix.length)];
      if (isNode(target)) {
        const { $ref: _ref, ...siblings } = node;
        return { ...target, ...siblings };
      }
    }
  }
  return node;
}

function props(node: unknown): Node {
  return isNode(node) && isNode(node.properties) ? node.properties : {};
}

function titleOf(node: unknown, key: string): string {
  if (isNode(node) && typeof node.title === "string" && node.title.trim()) return node.title;
  // Python's str.title(), as the backend uses: each run of letters capitalised.
  return key.replace(/_/g, " ").replace(/[A-Za-z]+/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase());
}

function critical(node: unknown): boolean {
  return isNode(node) && !!node["x-critical"];
}

// Same as the backend's has_value (required_fields.py).
export function hasValue(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return value.trim() !== "";
  if (Array.isArray(value)) return value.some(hasValue);
  if (isNode(value)) return Object.values(value).some(hasValue);
  return true;
}

const escape = (seg: string | number) => String(seg).replace(/~/g, "~0").replace(/\//g, "~1");
const unescape = (seg: string) => seg.replace(/~1/g, "/").replace(/~0/g, "~");

export function makeKey(path: (string | number)[]): string {
  return path.map(escape).join("/");
}

function listItems(node: Node, defs: Node): Node | null {
  if (node.type !== "array") return null;
  const items = resolve(node.items, defs);
  return isNode(items) && Object.keys(props(items)).length ? items : null;
}

export function reviewFields(schema: unknown, data: unknown): ReviewField[] {
  if (!isNode(schema)) return [];
  const defs = defsOf(schema);
  const record = isNode(data) ? data : {};
  const out: ReviewField[] = [];
  const add = (path: (string | number)[], label: string, isCritical: boolean, value: unknown) =>
    out.push({ key: makeKey(path), label, is_critical: isCritical, empty: !hasValue(value) });

  for (const [key, raw] of Object.entries(props(schema))) {
    const resolved = resolve(raw, defs);
    const node = isNode(resolved) ? resolved : {};
    const title = titleOf(node, key);
    const crit = critical(node);
    const value = record[key];
    const items = listItems(node, defs);
    if (items) {
      const entries = Array.isArray(value) ? value : [];
      if (entries.length === 0) add([key], title, crit, value);
      entries.forEach((entry, i) => {
        const obj = isNode(entry) ? entry : {};
        for (const [part, praw] of Object.entries(props(items))) {
          const pnode = resolve(praw, defs);
          add([key, i, part], `${title} ${i + 1} › ${titleOf(pnode, part)}`, crit || critical(pnode), obj[part]);
        }
      });
    } else if (node.type === "object" && Object.keys(props(node)).length) {
      const obj = isNode(value) ? value : {};
      for (const [part, praw] of Object.entries(props(node))) {
        const pnode = resolve(praw, defs);
        add([key, part], `${title} › ${titleOf(pnode, part)}`, crit || critical(pnode), obj[part]);
      }
    } else {
      add([key], title, crit, value);
    }
  }
  return out;
}

// Label for a stored field key from the schema alone, so a mark keeps its
// label after the record's data changes (and for marks made before fields
// were split per list entry, e.g. a whole "caveators" list).
export function labelFor(schema: unknown, key: string): string {
  if (!isNode(schema)) return key;
  const defs = defsOf(schema);
  const segs = key.split("/").map(unescape);
  let node: unknown = schema;
  const labels: string[] = [];
  segs.forEach((seg, i) => {
    node = resolve(node, defs);
    if (segs.length === 3 && i === 1 && /^\d+$/.test(seg)) {
      labels[labels.length - 1] = `${labels[labels.length - 1]} ${Number(seg) + 1}`;
      node = isNode(node) ? node.items : undefined;
      return;
    }
    const child = resolve(props(node)[seg], defs);
    labels.push(titleOf(child, seg));
    node = child;
  });
  return labels.join(" › ") || key;
}

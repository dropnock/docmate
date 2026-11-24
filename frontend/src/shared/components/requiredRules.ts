// What "required" means for indexed data: the field has a value. Mirrors
// backend/app/services/required_fields.py rule for rule — the server re-checks
// on Submit/Done and both are exercised by
// backend/tests/fixtures/required_fields_cases.json, so keep them in step.
//
// JSON Schema's own `required` only checks that a key is present, so "", []
// and {} pass — and SchemaForm pre-fills every list with [] and every object
// with {}, so a required list or object could never fail. Here a field named
// in a `required` array, at any depth, must hold a value:
//   - missing / null → missing (null is a value only if `type` allows "null")
//   - string → not blank after trimming
//   - list → at least one entry that holds a value
//   - object → at least one property that holds a value
//   - numbers / booleans → any value, including 0 and false
// Required fields inside list items are checked on every entry that exists;
// an optional list may still be empty. "x-hidden" properties are exempt.
// Only properties / required / items are walked.

type Json = unknown;
type Node = Record<string, unknown>;
export type RequiredPath = (string | number)[];

const isNode = (v: unknown): v is Node => !!v && typeof v === "object" && !Array.isArray(v);

function resolve(node: unknown, defs: Node): unknown {
  if (!isNode(node) || typeof node.$ref !== "string") return node;
  const ref = node.$ref;
  for (const prefix of ["#/definitions/", "#/$defs/"]) {
    if (ref.startsWith(prefix)) {
      const target = defs[ref.slice(prefix.length)];
      if (isNode(target)) {
        const { $ref: _ref, ...siblings } = node;
        return { ...target, ...siblings };
      }
    }
  }
  return node;
}

function allowsNull(schema: unknown): boolean {
  if (!isNode(schema)) return false;
  const t = schema.type;
  return t === "null" || (Array.isArray(t) && t.includes("null"));
}

export function hasValue(value: Json): boolean {
  if (value === undefined || value === null) return false;
  if (typeof value === "string") return value.trim() !== "";
  if (Array.isArray(value)) return value.some(hasValue);
  if (isNode(value)) return Object.values(value).some(hasValue);
  return true;
}

function walk(schemaIn: unknown, data: Json, path: RequiredPath, defs: Node, out: RequiredPath[]) {
  const schema = resolve(schemaIn, defs);
  if (!isNode(schema)) return;

  if (Array.isArray(data)) {
    const items = resolve(schema.items, defs);
    if (isNode(items)) data.forEach((item, i) => walk(items, item, [...path, i], defs, out));
    return;
  }
  if (!isNode(data) || !isNode(schema.properties)) return;
  const properties = schema.properties;

  for (const key of Array.isArray(schema.required) ? schema.required : []) {
    if (typeof key !== "string") continue;
    const prop = resolve(properties[key], defs);
    if (isNode(prop) && prop["x-hidden"]) continue;
    const value = data[key];
    if (value === null && key in data && allowsNull(prop)) continue;
    if (!hasValue(value)) out.push([...path, key]);
  }
  for (const [key, prop] of Object.entries(properties)) {
    if (key in data) walk(prop, data[key], [...path, key], defs, out);
  }
}

/** Paths (keys and list indexes) of required fields without a value. */
export function findMissingRequired(schema: unknown, data: Json): RequiredPath[] {
  if (!isNode(schema)) return [];
  const defs = {
    ...(isNode(schema.definitions) ? schema.definitions : {}),
    ...(isNode(schema.$defs) ? schema.$defs : {}),
  };
  const out: RequiredPath[] = [];
  walk(schema, isNode(data) ? data : {}, [], defs, out);
  return out;
}

const humanize = (key: string) => key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

/** Human label for a path, e.g. "Registered Titles › 2 › Folio Number" —
 * same format as required_fields.describe_path on the server. */
export function describeRequiredPath(schema: unknown, path: RequiredPath): string {
  if (!isNode(schema)) return path.join(" › ");
  const defs = {
    ...(isNode(schema.definitions) ? schema.definitions : {}),
    ...(isNode(schema.$defs) ? schema.$defs : {}),
  };
  let node: unknown = schema;
  const labels: string[] = [];
  for (const seg of path) {
    node = resolve(node, defs);
    if (typeof seg === "number") {
      labels.push(String(seg + 1));
      node = isNode(node) ? node.items : undefined;
    } else {
      const props = isNode(node) && isNode(node.properties) ? node.properties : undefined;
      node = resolve(props?.[seg], defs);
      labels.push(isNode(node) && typeof node.title === "string" ? node.title : humanize(seg));
    }
  }
  return labels.join(" › ");
}

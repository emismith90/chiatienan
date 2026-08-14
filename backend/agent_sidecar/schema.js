/**
 * JSON Schema → TypeBox, for the tool schemas `app/tools.py` owns.
 *
 * Pi wants TypeBox for a tool's `parameters`, and Python produces plain JSON
 * Schema. All schemas were dumped (`bench/dump_schemas.py`) and analysed: the
 * converter needs **exactly six keywords** and nothing more —
 *
 *   type · properties · required · items · description · enum
 *
 * so anything else is a signal the schemas changed, and this throws rather than
 * dropping it. A silently dropped constraint means the model sends arguments the
 * tool then rejects, which surfaces as a clarifying question instead of a logged
 * meal — a failure nobody would trace back to a converter.
 *
 * Two cases carry all the risk, and both have their own test:
 *
 * 1. **String enums must use `StringEnum`**, not `Type.Union` /`Type.Literal`.
 *    Pi's `docs/extensions.md` is explicit that the union form breaks Google's
 *    API. Six of them carry an enum, from three distinct definitions —
 *    `discount_split`, `keyword` (shared by reference across four tools), and
 *    `mode`.
 * 2. **One union type exists**: `{"type": ["string", "integer"]}` on `target`, in
 *    `_UPDATE_MEMBER_SCHEMA` and `_DELETE_MEMBER_SCHEMA` (`tools.py:193`, `:216`).
 *    It is the single case a naive converter mistranslates, so it maps to
 *    `Type.Union([...])` deliberately — an *enum* must not, but a genuine type
 *    union has no other spelling.
 */
import { StringEnum, Type } from "@earendil-works/pi-ai";

/** The only keywords the 14 schemas use. Anything else throws. */
export const SUPPORTED_KEYWORDS = new Set([
  "type",
  "properties",
  "required",
  "items",
  "description",
  "enum",
]);

const SCALARS = {
  string: (options) => Type.String(options),
  integer: (options) => Type.Integer(options),
  number: (options) => Type.Number(options),
  boolean: (options) => Type.Boolean(options),
};

function assertSupported(node, path) {
  for (const keyword of Object.keys(node)) {
    if (!SUPPORTED_KEYWORDS.has(keyword)) {
      throw new Error(
        `${path}: unsupported JSON Schema keyword ${JSON.stringify(keyword)}. ` +
          `Add it to schema.js deliberately — dropping it would let the model ` +
          `send arguments the tool rejects.`,
      );
    }
  }
}

/**
 * Convert one JSON Schema node to a TypeBox schema.
 *
 * @param {object} node
 * @param {string} [path] for error messages
 */
export function toTypeBox(node, path = "schema") {
  if (node === null || typeof node !== "object" || Array.isArray(node)) {
    throw new Error(`${path}: expected a JSON Schema object, got ${JSON.stringify(node)}`);
  }
  assertSupported(node, path);

  const options = node.description ? { description: node.description } : {};
  const declared = node.type;

  // The union: {"type": ["string", "integer"]}. Not an enum — a real type union,
  // which is the one thing Type.Union is still the right answer for.
  if (Array.isArray(declared)) {
    const members = declared.map((name) => {
      const build = SCALARS[name];
      if (!build) {
        throw new Error(`${path}: union member ${JSON.stringify(name)} is not a scalar type`);
      }
      return build({});
    });
    return Type.Union(members, options);
  }

  if (declared === "object") {
    const required = new Set(node.required || []);
    const properties = {};
    for (const [key, child] of Object.entries(node.properties || {})) {
      const converted = toTypeBox(child, `${path}.${key}`);
      properties[key] = required.has(key) ? converted : Type.Optional(converted);
    }
    return Type.Object(properties, options);
  }

  if (declared === "array") {
    if (!node.items) {
      throw new Error(`${path}: an array must declare items`);
    }
    return Type.Array(toTypeBox(node.items, `${path}[]`), options);
  }

  if (node.enum) {
    // StringEnum, never Type.Union/Type.Literal: the union form breaks Google's
    // API, and PI_VISION_MODEL is one environment variable away from being a
    // Google model.
    if (declared !== "string") {
      throw new Error(`${path}: enum is only supported on strings, not ${declared}`);
    }
    return StringEnum(node.enum, options);
  }

  const build = SCALARS[declared];
  if (!build) {
    throw new Error(`${path}: unsupported type ${JSON.stringify(declared)}`);
  }
  return build(options);
}

/** Convert a whole `{name: schema}` manifest. */
export function toTypeBoxManifest(schemas) {
  const out = {};
  for (const [name, schema] of Object.entries(schemas)) {
    out[name] = toTypeBox(schema, name);
  }
  return out;
}

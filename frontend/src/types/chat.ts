/** An image attachment on a user turn. `data` is raw base64 (no data: prefix);
 * derive a preview URL as `data:${mimeType};base64,${data}`. `data` may be ""
 * if the raw bytes were stripped upstream — render a fallback then. */
export interface ChatImage {
  data: string;
  mimeType: string;
  name?: string;
}

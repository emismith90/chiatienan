import type { ChatImage } from "@/types/chat";

/** Longest-edge target for an uploaded photo. ~1600px keeps printed bill text
 * legible for the vision turn while cutting a multi-MB phone photo down to a
 * few hundred KB — well under the backend's per-image / total size caps
 * (see backend/app/images.py). */
export const MAX_EDGE = 1600;
/** JPEG quality for the re-encoded photo. 0.75 is a good legibility/size
 * trade-off for document-like bill photos. */
export const JPEG_QUALITY = 0.75;

/** Compute the scaled-down (w, h) so the longest edge is at most `maxEdge`,
 * preserving aspect ratio. Never upscales. Pure — unit-tested in isolation. */
export function fitWithin(
  width: number,
  height: number,
  maxEdge: number = MAX_EDGE,
): { width: number; height: number } {
  const longest = Math.max(width, height);
  if (longest <= maxEdge || longest === 0) return { width, height };
  const scale = maxEdge / longest;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

/** Read a File into raw base64 (stripping the `data:<mime>;base64,` prefix),
 * keeping the original mime/name. Used as the fallback when canvas re-encoding
 * isn't possible (e.g. animated GIF, or a decode failure). */
function fileToImageRaw(file: File): Promise<ChatImage> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      const data = comma >= 0 ? result.slice(comma + 1) : result;
      resolve({ data, mimeType: file.type || "image/png", name: file.name });
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function loadBitmap(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = (e) => {
      URL.revokeObjectURL(url);
      reject(e);
    };
    img.src = url;
  });
}

/** Downscale + re-encode a picked image to JPEG so a big phone bill photo
 * uploads small but stays readable. Falls back to the original bytes for
 * animated GIFs (re-encoding would freeze them) and if anything in the
 * canvas path fails, so a send never breaks over a resize hiccup. */
export async function resizeImage(
  file: File,
  { maxEdge = MAX_EDGE, quality = JPEG_QUALITY }: { maxEdge?: number; quality?: number } = {},
): Promise<ChatImage> {
  // GIFs can be animated; canvas would flatten them to one frame. Leave as-is.
  if (file.type === "image/gif") return fileToImageRaw(file);

  try {
    const img = await loadBitmap(file);
    const { width, height } = fitWithin(img.naturalWidth || img.width, img.naturalHeight || img.height, maxEdge);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const cx = canvas.getContext("2d");
    if (!cx) return fileToImageRaw(file);
    cx.drawImage(img, 0, 0, width, height);
    const dataUrl = canvas.toDataURL("image/jpeg", quality);
    const comma = dataUrl.indexOf(",");
    if (comma < 0) return fileToImageRaw(file);
    const rawJpeg = dataUrl.slice(comma + 1);
    const rawOriginalLen = file.size; // bytes of the source
    // If re-encoding somehow produced something larger (tiny already-compressed
    // image), keep the smaller original.
    const jpegBytes = (rawJpeg.length * 3) / 4;
    if (jpegBytes >= rawOriginalLen && (img.naturalWidth || img.width) <= maxEdge) {
      return fileToImageRaw(file);
    }
    const base = (file.name || "photo").replace(/\.[^.]+$/, "");
    return { data: rawJpeg, mimeType: "image/jpeg", name: `${base}.jpg` };
  } catch {
    return fileToImageRaw(file);
  }
}

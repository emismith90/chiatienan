"use client";
import { useEffect, useState } from "react";

/**
 * An image that opens a full-screen lightbox when tapped, so small inline
 * images (e.g. transfer QR codes) can be enlarged for scanning. Tap anywhere
 * on the backdrop — or press Escape — to close.
 */
export function ZoomableImage({
  src,
  alt,
  className,
  width,
  height,
}: {
  src: string;
  alt: string;
  className?: string;
  width?: number;
  height?: number;
}) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    // Lock background scroll while the lightbox is open.
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Enlarge: ${alt}`}
        className="shrink-0 cursor-zoom-in rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
      >
        <img src={src} alt={alt} width={width} height={height} className={className} />
      </button>
      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={alt}
          onClick={() => setOpen(false)}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
        >
          <img
            src={src}
            alt={alt}
            className="max-h-[85vh] max-w-[90vw] rounded-xl bg-white object-contain p-3 shadow-2xl"
          />
        </div>
      )}
    </>
  );
}

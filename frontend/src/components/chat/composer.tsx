"use client";
import { useRef, useState } from "react";
import type { ChatImage } from "@/types/chat";

const MAX_IMAGES = 4;

/** Read a File into `{ data, mimeType, name }`, stripping the
 * `data:<mime>;base64,` prefix so only raw base64 is sent to the API. */
function fileToImage(file: File): Promise<ChatImage> {
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

interface ComposerProps {
  onSend: (text: string, images?: ChatImage[]) => Promise<any> | void;
}

export function Composer({ onSend }: ComposerProps) {
  const [text, setText] = useState("");
  const [images, setImages] = useState<ChatImage[]>([]);
  const [sending, setSending] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const canSend = (text.trim().length > 0 || images.length > 0) && !sending;

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    const room = MAX_IMAGES - images.length;
    const picked = Array.from(files).slice(0, Math.max(0, room));
    const next = await Promise.all(picked.map(fileToImage));
    setImages((prev) => [...prev, ...next].slice(0, MAX_IMAGES));
    if (fileRef.current) fileRef.current.value = "";
  }

  function removeImage(idx: number) {
    setImages((prev) => prev.filter((_, i) => i !== idx));
  }

  async function submit() {
    if (!canSend) return;
    const body = text.trim();
    const imgs = images;
    setSending(true);
    try {
      await onSend(body, imgs.length ? imgs : undefined);
      setText("");
      setImages([]);
    } finally {
      setSending(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-2 shadow-sm">
      {images.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2 px-1">
          {images.map((img, i) => (
            <div key={i} className="relative">
              <img
                src={`data:${img.mimeType};base64,${img.data}`}
                alt={img.name || "attachment"}
                className="h-16 w-16 rounded-md border border-[var(--border)] object-cover"
              />
              <button
                type="button"
                onClick={() => removeImage(i)}
                aria-label="Xóa ảnh"
                className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-[var(--text-primary)] text-xs text-[var(--bg-surface)] shadow-sm transition-colors duration-150 hover:bg-[var(--accent-primary)]"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="flex items-end gap-2">
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={images.length >= MAX_IMAGES}
          aria-label="Đính kèm ảnh"
          title={images.length >= MAX_IMAGES ? `Tối đa ${MAX_IMAGES} ảnh` : "Đính kèm ảnh"}
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-[var(--border)] text-lg text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--bg-base)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          📎
        </button>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder="Nhắn tin… (dùng @bot để gọi bot)"
          aria-label="Soạn tin nhắn"
          className="max-h-40 min-h-10 flex-1 resize-none rounded-lg border border-[var(--border)] bg-[var(--bg-base)] px-3 py-2 text-base text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
        />
        <button
          type="button"
          onClick={submit}
          disabled={!canSend}
          className="flex h-10 shrink-0 items-center justify-center rounded-lg bg-[var(--accent-primary)] px-4 text-sm font-medium text-white shadow-sm transition-colors duration-150 hover:bg-[var(--accent-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          Gửi
        </button>
      </div>
    </div>
  );
}

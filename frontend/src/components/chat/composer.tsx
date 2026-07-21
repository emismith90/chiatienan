"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChatImage } from "@/types/chat";
import { botHandle } from "@/lib/api";
import { resizeImage } from "@/lib/image";
import { mentionQuery, spliceMention, MentionDropdown } from "./mention-dropdown";
import { SuggestionChips } from "./suggestion-chips";

const MAX_IMAGES = 4;
/** Caret-position keys that only move the cursor (no `onChange`), so the
 * mention state has to be recomputed for them explicitly. */
const CARET_NAV_KEYS = new Set(["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"]);

interface MentionState {
  /** Index of the "@" in `text`. */
  start: number;
  /** Caret index right after the partial handle. */
  end: number;
  query: string;
  active: number;
}

interface ComposerProps {
  onSend: (text: string, images?: ChatImage[]) => Promise<any> | void;
}

export function Composer({ onSend }: ComposerProps) {
  const [text, setText] = useState("");
  const [images, setImages] = useState<ChatImage[]>([]);
  const [sending, setSending] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [notice, setNotice] = useState("");
  const [handles, setHandles] = useState<string[]>(["bot"]);
  const [mention, setMention] = useState<MentionState | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let live = true;
    botHandle().then((h) => {
      if (live) setHandles([h]);
    });
    return () => {
      live = false;
    };
  }, []);

  // Auto-grow the textarea with its content (bounded by the CSS max-h-40 =
  // 160px), so multi-line messages aren't clipped — most visible on mobile,
  // where the narrow field wraps sooner. Resets when `text` is cleared on send.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    // When empty, clear the inline height so the CSS min-height (min-h-10)
    // governs. Measuring an empty textarea during mount/hydration can report a
    // bogus tall scrollHeight (narrow pre-layout width → wrapped placeholder),
    // which would otherwise pin the box at max-h-40 forever.
    if (!text) {
      el.style.height = "";
      el.style.overflowY = "";
      return;
    }
    el.style.height = "auto";
    const sh = el.scrollHeight;
    el.style.height = `${Math.min(sh, 160)}px`;
    // Only show a scrollbar once content exceeds the max height; below that the
    // box grows to fit, so a scrollbar would just be visual noise.
    el.style.overflowY = sh > 160 ? "auto" : "hidden";
  }, [text]);

  const mentionItems = useMemo(() => {
    if (!mention) return [];
    const q = mention.query.toLowerCase();
    return handles.filter((h) => h.toLowerCase().startsWith(q));
  }, [mention, handles]);

  const canSend = (text.trim().length > 0 || images.length > 0) && !sending && !processing;

  /** Recompute the `@`-mention state from the textarea's current value and
   * caret position; clears it when the caret isn't inside a mention. */
  function recomputeMention(el: HTMLTextAreaElement) {
    const caret = el.selectionStart ?? el.value.length;
    const query = mentionQuery(el.value, caret);
    if (query === null) {
      setMention(null);
      return;
    }
    setMention({ start: caret - query.length - 1, end: caret, query, active: 0 });
  }

  /** Prefill the composer from a capability chip and focus the input with the
   * caret at the end, so the user can complete/edit before sending (never
   * auto-sends). */
  function pickSuggestion(prefill: string) {
    setText(prefill);
    setMention(null);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(prefill.length, prefill.length);
    });
  }

  function acceptMention(handle: string) {
    if (!mention) return;
    const { next, caret } = spliceMention(text, mention.start, mention.end, handle);
    setText(next);
    setMention(null);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(caret, caret);
    });
  }

  async function handleFiles(files: FileList | File[] | null) {
    if (!files || files.length === 0) return;
    // Filter to images so a paste (which can carry non-image files) matches the
    // file picker's `accept="image/*"` constraint.
    const imageFiles = Array.from(files).filter((f) => f.type.startsWith("image/"));
    if (imageFiles.length === 0) return;
    setNotice("");
    const room = MAX_IMAGES - images.length;
    const picked = imageFiles.slice(0, Math.max(0, room));
    const dropped = imageFiles.length - picked.length;
    // Downscale each pick on-device (camera photos are multi-MB) before it ever
    // hits the wire; keeps bill text legible but well under the server caps.
    setProcessing(true);
    try {
      const next = await Promise.all(picked.map((f) => resizeImage(f)));
      setImages((prev) => [...prev, ...next].slice(0, MAX_IMAGES));
      if (dropped > 0) setNotice(`Chỉ đính kèm được tối đa ${MAX_IMAGES} ảnh.`);
    } catch {
      setNotice("Không xử lý được ảnh, thử lại nhé.");
    } finally {
      setProcessing(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  /** Paste an image straight into the composer (screenshots, copied images),
   * routed through the same attach path as the file picker. Only swallow the
   * paste when it actually carries an image, so pasting text still works. */
  function onPaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    const files = e.clipboardData?.files;
    if (files && Array.from(files).some((f) => f.type.startsWith("image/"))) {
      e.preventDefault();
      handleFiles(files);
    }
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
    if (mention && mentionItems.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMention((m) => (m ? { ...m, active: (m.active + 1) % mentionItems.length } : m));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMention((m) =>
          m ? { ...m, active: (m.active - 1 + mentionItems.length) % mentionItems.length } : m,
        );
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        acceptMention(mentionItems[mention.active]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMention(null);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      // On touch devices the on-screen keyboard's return key should insert a
      // newline (multi-line messages are common); sending is the Send button's
      // job there. On a physical keyboard, Enter still sends.
      if (window.matchMedia?.("(pointer: coarse)").matches) return;
      e.preventDefault();
      submit();
    }
  }

  function onKeyUp(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (CARET_NAV_KEYS.has(e.key)) recomputeMention(e.currentTarget);
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
                aria-label="Remove image"
                className="absolute -right-2 -top-2 flex h-6 w-6 items-center justify-center rounded-full bg-[var(--text-primary)] text-sm text-[var(--bg-surface)] shadow-sm transition-colors duration-150 hover:bg-[var(--accent-primary)]"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
      {(processing || notice) && (
        <p
          role="status"
          className="mb-2 px-1 text-xs text-[var(--text-secondary)]"
        >
          {processing ? "Đang xử lý ảnh…" : notice}
        </p>
      )}
      {/* Capability hints — shown only while the input is empty, so they guide
          newcomers without cluttering an active compose. */}
      {!text.trim() && (
        <div className="mb-2 px-1">
          <SuggestionChips onPick={pickSuggestion} />
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
          disabled={images.length >= MAX_IMAGES || processing}
          aria-label="Attach image"
          title={images.length >= MAX_IMAGES ? `Max ${MAX_IMAGES} images` : "Attach image"}
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-[var(--border)] text-lg text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--bg-base)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          📎
        </button>
        <div className="relative flex-1">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              recomputeMention(e.target);
            }}
            onKeyDown={onKeyDown}
            onKeyUp={onKeyUp}
            onPaste={onPaste}
            onClick={(e) => recomputeMention(e.currentTarget)}
            onBlur={() => setMention(null)}
            rows={1}
            placeholder="Message… (@bot)"
            aria-label="Compose message"
            aria-expanded={mention !== null && mentionItems.length > 0}
            aria-haspopup="listbox"
            className="block max-h-40 min-h-10 w-full resize-none overflow-y-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-base)] px-3 py-1.5 text-base leading-6 text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)]"
          />
          {mention && mentionItems.length > 0 && (
            <MentionDropdown items={mentionItems} active={mention.active} onPick={acceptMention} />
          )}
        </div>
        <button
          type="button"
          onClick={submit}
          disabled={!canSend}
          className="flex h-10 shrink-0 items-center justify-center rounded-lg bg-[var(--accent-primary)] px-4 text-sm font-medium text-white shadow-sm transition-colors duration-150 hover:bg-[var(--accent-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          Send
        </button>
      </div>
    </div>
  );
}

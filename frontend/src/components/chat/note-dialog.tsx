"use client";
import { useState } from "react";
import * as api from "@/lib/api";
import type { GateKind, KnowledgeData, KnowledgeNote } from "@/lib/api";
import {
  Field, PanelDialog, fieldClass, isConflict, primaryButtonClass, quietButtonClass, writeError,
} from "./knowledge-ui";

/** The three clock rules `observations.gate_status` can evaluate, as a picker.
 *
 * `busy` and `closes` are travel-aware (can you *get there* in time) and
 * `order-by` is not (you phone ahead), which is why they are three verbs and not
 * one "deadline" field — the room told the wrong one wastes a walk. */
const GATES: { value: GateKind | ""; label: string }[] = [
  { value: "", label: "None" },
  { value: "order-by", label: "Order by…" },
  { value: "busy", label: "Busy from…" },
  { value: "closes", label: "Closes…" },
];

const today = () => new Date().toISOString().slice(0, 10);

/**
 * Add, edit or delete one remembered fact.
 *
 * The `standing` toggle is the load-bearing control: it decides whether the line
 * decays. A dated observation is weak evidence after six months; "phải gọi trước
 * 11h30" is as true next year as today, and design D4 separates the two with this
 * one field. So it is a visible choice with its consequence spelled out, not a
 * checkbox called "always".
 *
 * The subject cannot be changed on an existing note: a note about a different
 * restaurant is a different fact. Delete and add.
 *
 * The text is a single-line input on purpose — the file is one line per fact, and
 * a textarea would invite paragraphs the server then has to flatten.
 */
export function NoteDialog({
  roomId, note, subjects, etag, presetSubject, onClose, onSaved, onConflict,
}: {
  roomId: number;
  /** null = creating. */
  note: KnowledgeNote | null;
  subjects: KnowledgeData["subjects"];
  etag: string;
  /** Pre-selected subject when adding from a place's own card. */
  presetSubject?: string;
  onClose: () => void;
  onSaved: () => void;
  /** Someone else changed the file since we read it — reload and tell the user. */
  onConflict: () => void;
}) {
  const [subject, setSubject] = useState(
    note?.subject ?? presetSubject ?? subjects[0]?.subject ?? "",
  );
  const [text, setText] = useState(note?.text ?? "");
  const [standing, setStanding] = useState(note?.standing ?? false);
  const [when, setWhen] = useState(note?.when ?? today());
  const [gateKind, setGateKind] = useState<GateKind | "">(note?.gate_kind ?? "");
  // `gate` is the stored form ("order-by@11:30"); the time input wants just "11:30".
  const [gateAt, setGateAt] = useState(note?.gate?.split("@")[1] ?? "11:30");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const places = subjects.filter((s) => s.kind === "place");
  const members = subjects.filter((s) => s.kind === "member");

  async function run(fn: Promise<unknown>, fail: string) {
    setBusy(true);
    setErr("");
    try {
      await fn;
      onSaved();
      onClose();
    } catch (e) {
      setErr(writeError(e, fail));
      if (isConflict(e)) onConflict();
    } finally {
      setBusy(false);
    }
  }

  function save() {
    if (!text.trim()) {
      setErr("A note needs some text.");
      return;
    }
    const fields = {
      text: text.trim(),
      standing,
      when: standing ? null : when,
      gate_kind: gateKind || null,
      gate_at: gateKind ? gateAt : null,
    };
    if (note) {
      run(api.patchNote(roomId, note.id, { ...fields, etag }), "Could not save — try again.");
    } else {
      if (!subject) {
        setErr("Pick a place or a person first.");
        return;
      }
      run(api.createNote(roomId, { ...fields, subject }), "Could not save — try again.");
    }
  }

  const actions = (
    <>
      {err && <p className="mb-2 text-xs text-[var(--danger)]">{err}</p>}
      <div className="flex gap-2">
        <button type="button" disabled={busy} onClick={save} className={primaryButtonClass}>
          {note ? "Save" : "Remember"}
        </button>
        <button type="button" disabled={busy} onClick={onClose} className={quietButtonClass}>
          Cancel
        </button>
      </div>
    </>
  );

  return (
    <PanelDialog label={note ? "Edit note" : "Add note"} onClose={onClose}
                 footer={actions}>
      <h3 className="text-base font-semibold text-[var(--text-primary)]">
        {note ? "Edit note" : "Add note"}
      </h3>

      <div className="mt-4 space-y-3">
        {note ? (
          <p className="text-xs text-[var(--text-secondary)]">
            About <span className="font-medium text-[var(--text-primary)]">{note.subject_label}</span>
            {" — to file it under someone else, delete this and add it again."}
          </p>
        ) : (
          <Field label="About which place or person">
            <select className={fieldClass} value={subject}
                    onChange={(e) => setSubject(e.target.value)}>
              {places.length > 0 && (
                <optgroup label="Places">
                  {places.map((s) => (
                    <option key={s.subject} value={s.subject}>{s.label}</option>
                  ))}
                </optgroup>
              )}
              {members.length > 0 && (
                <optgroup label="People">
                  {members.map((s) => (
                    <option key={s.subject} value={s.subject}>{s.label}</option>
                  ))}
                </optgroup>
              )}
            </select>
          </Field>
        )}

        <Field label="Note">
          <input className={fieldClass} value={text} onChange={(e) => setText(e.target.value)}
                 placeholder="Phải gọi trước — quán làm chậm." />
        </Field>

        <div>
          <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
            Kind
          </span>
          <div className="flex overflow-hidden rounded-lg border border-[var(--border)] text-xs">
            <button type="button" onClick={() => setStanding(true)} aria-pressed={standing}
                    className={`flex-1 px-2.5 py-1.5 ${standing ? "bg-[var(--accent-primary)] font-semibold text-white" : "text-[var(--text-secondary)]"}`}>
              Standing rule
            </button>
            <button type="button" onClick={() => setStanding(false)} aria-pressed={!standing}
                    className={`flex-1 px-2.5 py-1.5 ${!standing ? "bg-[var(--accent-primary)] font-semibold text-white" : "text-[var(--text-secondary)]"}`}>
              One day
            </button>
          </div>
          <p className="mt-1 text-[10px] text-[var(--text-secondary)]">
            {standing
              ? "A standing rule always holds — the bot never drops it for being old."
              : "A one-day note fades: after 6 months the bot stops reading it."}
          </p>
        </div>

        {!standing && (
          <Field label="Date">
            <input type="date" className={fieldClass} value={when}
                   onChange={(e) => setWhen(e.target.value)} />
          </Field>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Field label="Clock rule">
            <select className={fieldClass} value={gateKind}
                    onChange={(e) => setGateKind(e.target.value as GateKind | "")}>
              {GATES.map((g) => (
                <option key={g.value} value={g.value}>{g.label}</option>
              ))}
            </select>
          </Field>
          {gateKind && (
            <Field label="At">
              <input type="time" className={fieldClass} value={gateAt}
                     onChange={(e) => setGateAt(e.target.value)} />
            </Field>
          )}
        </div>
        {gateKind && (
          <p className="text-[10px] text-[var(--text-secondary)]">
            The bot works this out against the current time — past it, the place stops
            being suggested.
          </p>
        )}
      </div>

      {note && (
        <div className="mt-4 border-t border-[var(--border)] pt-3">
          {confirmDelete ? (
            <div className="flex items-center gap-2">
              <p className="flex-1 text-xs text-[var(--text-secondary)]">Delete this note for good?</p>
              <button type="button" disabled={busy}
                      onClick={() => run(api.deleteNote(roomId, note.id, etag), "Could not delete.")}
                      className="rounded-lg border border-[var(--danger)] px-3 py-1.5 text-xs text-[var(--danger)] disabled:opacity-40">
                Delete
              </button>
            </div>
          ) : (
            <button type="button" onClick={() => setConfirmDelete(true)}
                    className="text-xs text-[var(--danger)]">
              Delete this note
            </button>
          )}
        </div>
      )}
    </PanelDialog>
  );
}

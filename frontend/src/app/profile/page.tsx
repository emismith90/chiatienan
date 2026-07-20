"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import * as api from "@/lib/api";
import { useSession } from "@/lib/session";

type Me = {
  id: number;
  display_name: string;
  nickname: string;
  bank_code: string | null;
  account_number: string | null;
  account_holder: string | null;
};

const inputClass =
  "w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] focus:outline-none focus-visible:ring-2 ring-[var(--accent-primary)] transition-all duration-150";

export default function ProfilePage() {
  const router = useRouter();
  const { signOut } = useSession();
  const [nickname, setNickname] = useState("");
  const [f, setF] = useState({
    display_name: "",
    bank_code: "",
    account_number: "",
    account_holder: "",
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    let live = true;
    api
      .getMe()
      .then((m: Me) => {
        if (!live) return;
        setNickname(m.nickname);
        setF({
          display_name: m.display_name || "",
          bank_code: m.bank_code || "",
          account_number: m.account_number || "",
          account_holder: m.account_holder || "",
        });
      })
      .catch((e) => live && setErr(e instanceof Error ? e.message : "Không tải được hồ sơ."))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, []);

  function set<K extends keyof typeof f>(key: K, value: string) {
    setSaved(false);
    setF((prev) => ({ ...prev, [key]: value }));
  }

  async function save() {
    setErr("");
    setSaved(false);
    setSaving(true);
    try {
      await api.updateMe(f);
      setSaved(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Không lưu được, thử lại.");
    } finally {
      setSaving(false);
    }
  }

  function handleSignOut() {
    signOut();
    router.push("/");
  }

  return (
    <main className="min-h-dvh pt-safe pb-safe bg-[var(--bg-base)] flex justify-center p-4">
      <div className="bg-[var(--bg-surface)] rounded-lg border border-[var(--border)] shadow-md max-w-md w-full p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold text-[var(--text-primary)]">Hồ sơ</h1>
          <Link
            href="/"
            className="text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors duration-150"
          >
            ← Quay lại
          </Link>
        </div>

        {loading ? (
          <p className="text-sm text-[var(--text-secondary)]">Đang tải…</p>
        ) : (
          <>
            <div className="space-y-3">
              <div>
                <label htmlFor="pf-nickname" className="mb-1 block text-xs text-[var(--text-secondary)]">
                  Biệt danh
                </label>
                <input
                  id="pf-nickname"
                  value={nickname}
                  disabled
                  readOnly
                  className={`${inputClass} opacity-60 cursor-not-allowed`}
                />
              </div>
              <div>
                <label htmlFor="pf-display-name" className="mb-1 block text-xs text-[var(--text-secondary)]">
                  Tên hiển thị
                </label>
                <input
                  id="pf-display-name"
                  placeholder="Tên hiển thị"
                  value={f.display_name}
                  onChange={(e) => set("display_name", e.target.value)}
                  className={inputClass}
                />
              </div>
              <div>
                <label htmlFor="pf-bank-code" className="mb-1 block text-xs text-[var(--text-secondary)]">
                  Mã ngân hàng
                </label>
                <input
                  id="pf-bank-code"
                  placeholder="Mã ngân hàng (vd VCB)"
                  value={f.bank_code}
                  onChange={(e) => set("bank_code", e.target.value)}
                  className={inputClass}
                />
              </div>
              <div>
                <label htmlFor="pf-account-number" className="mb-1 block text-xs text-[var(--text-secondary)]">
                  Số tài khoản
                </label>
                <input
                  id="pf-account-number"
                  placeholder="Số tài khoản"
                  value={f.account_number}
                  onChange={(e) => set("account_number", e.target.value)}
                  className={inputClass}
                />
              </div>
              <div>
                <label htmlFor="pf-account-holder" className="mb-1 block text-xs text-[var(--text-secondary)]">
                  Tên chủ tài khoản
                </label>
                <input
                  id="pf-account-holder"
                  placeholder="Tên chủ tài khoản"
                  value={f.account_holder}
                  onChange={(e) => set("account_holder", e.target.value)}
                  className={inputClass}
                />
              </div>
            </div>

            {err && (
              <p role="alert" className="text-sm text-[var(--accent-text)]">
                {err}
              </p>
            )}
            {saved && !err && (
              <p className="text-sm text-[var(--text-secondary)]">Đã lưu</p>
            )}

            <div className="space-y-2">
              <button
                type="button"
                onClick={save}
                disabled={saving}
                className="w-full rounded-md bg-[var(--accent-primary)] hover:bg-[var(--accent-hover)] text-white py-2 transition-all duration-150 disabled:opacity-50"
              >
                {saving ? "Đang lưu…" : "Lưu"}
              </button>
              <button
                type="button"
                onClick={handleSignOut}
                className="w-full rounded-md border border-[var(--border)] text-[var(--text-primary)] py-2 transition-all duration-150 hover:bg-[var(--bg-base)]"
              >
                Đăng xuất
              </button>
            </div>
          </>
        )}
      </div>
    </main>
  );
}

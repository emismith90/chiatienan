import { describe, expect, it } from "vitest";
import {
  appForBankCode,
  buildDeeplink,
  detectPlatform,
  listApps,
  normalizeBankCode,
  parseQrUrl,
} from "../deeplink";

const ANDROID_UA =
  "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Mobile Safari/537.36";
const IPHONE_UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";
const DESKTOP_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36";

describe("parseQrUrl", () => {
  // The shape backend/app/qr.py builds. If that format changes, this breaks
  // first — which is the point.
  const real =
    "https://img.vietqr.io/image/TPB-03924686701-compact2.png" +
    "?amount=107000&addInfo=Linh%3A%20T2%20bun%20cha&accountName=NGUYEN%20VAN%20A";

  it("recovers bank, account and holder from a settlement QR URL", () => {
    expect(parseQrUrl(real)).toEqual({
      bankCode: "TPB",
      accountNumber: "03924686701",
      accountName: "NGUYEN VAN A",
    });
  });

  it("tolerates a QR URL with no query string", () => {
    expect(parseQrUrl("https://img.vietqr.io/image/VCB-123-compact2.png")).toEqual({
      bankCode: "VCB",
      accountNumber: "123",
      accountName: "",
    });
  });

  it("keeps hyphens that belong to the account number", () => {
    expect(parseQrUrl("https://img.vietqr.io/image/ICB-12-34-qr_only.png")?.accountNumber)
      .toBe("12-34");
  });

  it("honours a custom QR_BASE_URL host", () => {
    expect(parseQrUrl("https://qr.example.test/img/MB-9001-print.png")?.bankCode).toBe("MB");
  });

  it("returns null on junk rather than half a payee", () => {
    expect(parseQrUrl(null)).toBeNull();
    expect(parseQrUrl("")).toBeNull();
    expect(parseQrUrl("not a url")).toBeNull();
    expect(parseQrUrl("https://img.vietqr.io/image/TPB.png")).toBeNull();
  });
});

describe("normalizeBankCode", () => {
  it("accepts the code members are told to type", () => {
    expect(normalizeBankCode("VCB")).toBe("VCB");
    expect(normalizeBankCode("tpb")).toBe("TPB");
  });

  it("accepts a BIN, which the QR image endpoint also allows", () => {
    expect(normalizeBankCode("970436")).toBe("VCB");
  });

  it("accepts the short name people actually say", () => {
    expect(normalizeBankCode("Vietcombank")).toBe("VCB");
    expect(normalizeBankCode(" techcombank ")).toBe("TCB");
  });

  it("returns null for an unknown bank instead of guessing", () => {
    expect(normalizeBankCode("NOTABANK")).toBeNull();
    expect(normalizeBankCode("")).toBeNull();
    expect(normalizeBankCode(null)).toBeNull();
  });
});

describe("appForBankCode", () => {
  it("maps a bank to its app", () => {
    expect(appForBankCode("TPB")?.appId).toBe("tpb");
    expect(appForBankCode("970436")?.appId).toBe("vcb");
  });

  it("prefers the flagship app over a secondary one", () => {
    // The snapshot also carries tpb-pay / acb-biz / vib-2; a bank code must
    // never resolve to those.
    expect(appForBankCode("TPB")?.appId).not.toBe("tpb-pay");
    expect(appForBankCode("ACB")?.appId).toBe("acb");
  });

  it("has no app for the 32 banks VietQR does not list", () => {
    // Sacombank is a real bank with a real app — just not in the deeplink list.
    // Better no button than a button that goes nowhere.
    expect(normalizeBankCode("STB")).toBe("STB");
    expect(appForBankCode("STB")).toBeNull();
  });
});

describe("buildDeeplink", () => {
  const payee = { bankCode: "TPB", accountNumber: "03924686701", accountName: "NGUYEN VAN A" };

  it("carries payer app, payee account, amount and note", () => {
    const url = buildDeeplink("vcb", payee, 107_000, "Linh: T2 bun cha");
    expect(url).toBe(
      "https://dl.vietqr.io/pay?app=vcb&ba=03924686701@tpb&am=107000" +
        "&tn=Linh%3A%20T2%20bun%20cha&bn=NGUYEN%20VAN%20A",
    );
  });

  it("keeps the payer's app distinct from the payee's bank", () => {
    // Paying a TPBank payee from a Vietcombank app: app=vcb, ba=...@tpb.
    const url = new URL(buildDeeplink("vcb", payee, 1000, ""));
    expect(url.searchParams.get("app")).toBe("vcb");
    expect(url.searchParams.get("ba")).toBe("03924686701@tpb");
  });

  it("normalises a BIN in the payee's bank code", () => {
    const url = buildDeeplink("mb", { ...payee, bankCode: "970436" }, 1000, "x");
    expect(url).toContain("ba=03924686701@vcb");
  });

  it("falls back to the raw bank code when it is unknown upstream", () => {
    const url = buildDeeplink("mb", { ...payee, bankCode: "WEIRD" }, 1000, "x");
    expect(url).toContain("ba=03924686701@weird");
  });

  it("omits empty note and holder rather than sending blanks", () => {
    const url = buildDeeplink("mb", { ...payee, accountName: "" }, 1000, "");
    expect(url).not.toContain("tn=");
    expect(url).not.toContain("bn=");
  });

  it("sends a whole-dong integer amount", () => {
    expect(buildDeeplink("mb", payee, 107_000.4, "x")).toContain("am=107000");
  });
});

describe("detectPlatform", () => {
  it("recognises the two mobile platforms", () => {
    expect(detectPlatform(ANDROID_UA)).toBe("android");
    expect(detectPlatform(IPHONE_UA)).toBe("ios");
  });

  it("treats desktop as unsupported — nothing to hand off to", () => {
    expect(detectPlatform(DESKTOP_UA)).toBe("other");
  });
});

describe("listApps", () => {
  it("sorts by popularity so the common banks are reachable first", () => {
    const ids = listApps("android").map((a) => a.appId);
    expect(ids[0]).toBe("mb");
    expect(ids.indexOf("vcb")).toBeLessThan(ids.indexOf("ocb"));
  });

  it("offers every app on both platforms", () => {
    expect(listApps("ios").length).toBe(listApps("android").length);
    expect(listApps("android").length).toBeGreaterThan(30);
  });
});

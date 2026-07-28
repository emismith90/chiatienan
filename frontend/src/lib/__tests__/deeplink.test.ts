import { describe, expect, it } from "vitest";
import {
  APPS,
  appById,
  appForBankCode,
  detectPlatform,
  listApps,
  normalizeBankCode,
  appLaunchUrl,
  parseQrUrl,
  storeUrlFor,
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

describe("appLaunchUrl", () => {
  it("opens the bank's own scheme on iOS", () => {
    // The registered one. `vietqr://pay?…`, which VietQR documents and which
    // would have carried the transfer, is unhandled on a real device — an
    // iPhone with Techcombank installed answered it with "the address is
    // invalid". Asserting the bank scheme here is what keeps it from coming back.
    expect(appLaunchUrl(appById("tcb")!, "ios")).toBe("tcb://");
    expect(appLaunchUrl(appById("tpb")!, "ios")).toBe("hydro://");
  });

  it("never launches the vietqr scheme, on either platform", () => {
    for (const platform of ["ios", "android"] as const) {
      for (const app of APPS) {
        expect(appLaunchUrl(app, platform), `${app.appId} ${platform}`)
          .not.toContain("vietqr:");
      }
    }
  });

  it("opens the intent URL on Android, with a Play Store fallback spliced in", () => {
    const url = appLaunchUrl(appById("vcb")!, "android")!;
    expect(url.startsWith("intent://")).toBe(true);
    expect(url).toContain("scheme=vietcombankmobile");
    expect(url).toContain("package=com.VCB");
    // Chrome resolves this itself when the package is absent, so iOS's timer is
    // not needed there.
    expect(url).toContain(
      "S.browser_fallback_url=" +
        encodeURIComponent("https://play.google.com/store/apps/details?id=com.VCB"),
    );
    // `end` must stay the terminator or Chrome rejects the whole URL.
    expect(url.endsWith(";end")).toBe(true);
  });

  it("has nothing to open on desktop", () => {
    expect(appLaunchUrl(appById("tcb")!, "other")).toBeNull();
  });

  it("carries no transfer details, because no scheme we can use accepts any", () => {
    for (const platform of ["ios", "android"] as const) {
      const url = appLaunchUrl(appById("tcb")!, platform)!;
      for (const leak of ["am=", "ba=", "tn=", "bn=", "107000", "03924686701"]) {
        expect(url).not.toContain(leak);
      }
    }
  });

  it("resolves a launch target and a store page for every app in the snapshot", () => {
    // The snapshot is scraped; a silent regression there would disable the
    // button for a whole bank without failing anything else.
    for (const app of APPS) {
      expect(appLaunchUrl(app, "ios"), `${app.appId} ios`).toBeTruthy();
      expect(appLaunchUrl(app, "android"), `${app.appId} android`).toBeTruthy();
      expect(storeUrlFor(app, "ios"), `${app.appId} store ios`).toBeTruthy();
      expect(storeUrlFor(app, "android"), `${app.appId} store android`).toBeTruthy();
    }
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

#!/usr/bin/env python3
"""Regenerate `src/lib/bank-apps.json` from the VietQR deeplink + bank APIs.

Run by hand when the app list drifts, then commit the diff:

    python3 frontend/scripts/gen-bank-apps.py

Three upstream endpoints plus one scrape, merged into one snapshot:

  /v2/android-app-deeplinks   37 apps
  /v2/ios-app-deeplinks       same 37 appIds; only the logos (and a few
                              monthlyInstall counts) differ
  /v2/banks                   65 banks — needed to normalise whatever a member
                              typed into their `bank_code` profile field (a
                              code, a BIN, or a short name) down to a code we
                              can match against an appId.
  dl.vietqr.io/pay?app=<id>   the launch target per platform, which the API does
                              NOT expose: its `deeplink` field is only ever
                              `https://dl.vietqr.io/pay?app=<id>`, i.e. a pointer
                              back at the redirector. Fetching that page once per
                              app yields the real target — see below.

## Why we scrape the launch targets instead of using the redirector

`dl.vietqr.io/pay` is an interstitial, and it hands the bank app **nothing**.
On iOS it serves HTML whose script does `location.href = '<scheme>://'`; on
Android it 301s to `intent://…#Intent;scheme=…;package=…;end`. Neither carries
the account, amount or note we pass in `ba`/`am`/`tn`/`bn` — those stop at
VietQR. Confirmed across all 37 apps, both platforms.

Two consequences, both deliberate:

  1. There is no URL that pre-fills a transfer. The only channel that carries
     the amount and description is the QR payload itself (NAPAS "Quy định
     Định dạng mã VietQR" v1.0 §5.2.6 ID 54, §5.2.14 ID 62-08), which reaches
     the app by being scanned — not by being linked to.
  2. Since the redirector adds nothing, we skip it and navigate to the target
     directly. That removes the visible interstitial, and a third party from
     the tap path.

Why a committed snapshot rather than a runtime fetch: the list changes a few
times a year, it is a few KB, and a settlement card must not wait on a
third-party request to render its pay button.

The upstream `autofill` flag is recorded but deliberately unused — see
`src/lib/deeplink.ts` for why it is not trustworthy enough to branch on.
"""
import json
import os
import re
import urllib.error
import urllib.request

ANDROID = "https://api.vietqr.io/v2/android-app-deeplinks"
IOS = "https://api.vietqr.io/v2/ios-app-deeplinks"
BANKS = "https://api.vietqr.io/v2/banks"
REDIRECTOR = "https://dl.vietqr.io/pay?app={app_id}"

# The redirector branches on User-Agent, so ask it as each platform in turn.
UA_IOS = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
UA_ANDROID = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

OUT = os.path.join(os.path.dirname(__file__), "..", "src", "lib", "bank-apps.json")


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep the 301 so we can read its Location rather than chase it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def launch_targets(app_id: str) -> dict:
    """The real launch target per platform, scraped from the redirector.

    iOS: the page embeds ``const DEEPLINK = '<scheme>://'`` and a
    ``const STORE_URL`` to fall back to when the app is absent.
    Android: a 301 to an ``intent://`` URL carrying the scheme and package.
    """
    url = REDIRECTOR.format(app_id=app_id)
    out = {"schemeIos": None, "storeIos": None, "intentAndroid": None,
           "packageAndroid": None}

    req = urllib.request.Request(url, headers={"User-Agent": UA_IOS})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", "replace")
        m = re.search(r"const DEEPLINK = '([^']*)'", html)
        out["schemeIos"] = m.group(1) if m else None
        m = re.search(r"const STORE_URL = '([^']*)'", html)
        out["storeIos"] = m.group(1) if m else None
    except urllib.error.URLError as exc:
        print(f"  ! {app_id}: iOS scrape failed ({exc})")

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": UA_ANDROID})
    try:
        with opener.open(req, timeout=30) as resp:
            loc = resp.headers.get("Location")
    except urllib.error.HTTPError as exc:
        loc = exc.headers.get("Location")
    except urllib.error.URLError as exc:
        print(f"  ! {app_id}: Android scrape failed ({exc})")
        loc = None
    if loc and loc.startswith("intent://"):
        out["intentAndroid"] = loc
        m = re.search(r"package=([^;]+)", loc)
        out["packageAndroid"] = m.group(1) if m else None
    return out


def clean(name: str) -> str:
    """iOS app names arrive prefixed with U+200E (LTR mark). Strip it."""
    return name.replace("‎", "").strip()


def main() -> None:
    android = {a["appId"]: a for a in get(ANDROID)["apps"]}
    ios = {a["appId"]: a for a in get(IOS)["apps"]}
    banks = get(BANKS)["data"]

    apps = []
    for app_id in sorted(android.keys() | ios.keys()):
        a, i = android.get(app_id), ios.get(app_id)
        any_ = a or i
        apps.append({
            "appId": app_id,
            # Android names are the tidier of the two ("SmartBanking" vs
            # "BIDV SmartBanking"), so prefer them where both exist.
            "appName": clean((a or i)["appName"]),
            "bankName": any_["bankName"],
            # Only ever a sort key. The two platforms disagree; take the larger
            # so an app is not buried because one store under-reports.
            "monthlyInstall": max(x["monthlyInstall"] for x in (a, i) if x),
            "autofill": bool(any_["autofill"]),
            "android": bool(a),
            "ios": bool(i),
            "logoAndroid": a["appLogo"] if a else None,
            "logoIos": i["appLogo"] if i else None,
            # Filled below; one request per platform per app.
            "schemeIos": None,
            "storeIos": None,
            "intentAndroid": None,
            "packageAndroid": None,
        })

    print(f"scraping launch targets for {len(apps)} apps…")
    for app in apps:
        app.update(launch_targets(app["appId"]))
    missing = [a["appId"] for a in apps
               if not (a["schemeIos"] or a["intentAndroid"])]
    if missing:
        print(f"  ! no launch target at all for: {', '.join(missing)}")

    app_ids = {a["appId"] for a in apps}
    # Only banks we can actually resolve to something useful are worth shipping:
    # every bank is a possible payee, so keep them all for BIN/short-name
    # normalisation, and flag which ones have an app.
    bank_rows = [{
        "code": b["code"],
        "bin": b["bin"],
        "shortName": b["shortName"],
        "hasApp": b["code"].lower() in app_ids,
    } for b in sorted(banks, key=lambda b: b["code"])]

    snapshot = {
        "_generated_by": "frontend/scripts/gen-bank-apps.py",
        "_sources": [ANDROID, IOS, BANKS, REDIRECTOR],
        "apps": apps,
        "banks": bank_rows,
    }

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    with_app = sum(1 for b in bank_rows if b["hasApp"])
    ios_ok = sum(1 for a in apps if a["schemeIos"])
    and_ok = sum(1 for a in apps if a["intentAndroid"])
    print(f"wrote {os.path.normpath(OUT)}")
    print(f"  {len(apps)} apps, {len(bank_rows)} banks ({with_app} with an app)")
    print(f"  launch targets: {ios_ok} iOS schemes, {and_ok} Android intents")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Regenerate `src/lib/bank-apps.json` from the VietQR deeplink + bank APIs.

Run by hand when the app list drifts, then commit the diff:

    python3 frontend/scripts/gen-bank-apps.py

Three upstream endpoints, merged into one snapshot:

  /v2/android-app-deeplinks   37 apps
  /v2/ios-app-deeplinks       same 37 appIds; only the logos (and a few
                              monthlyInstall counts) differ
  /v2/banks                   65 banks — needed to normalise whatever a member
                              typed into their `bank_code` profile field (a
                              code, a BIN, or a short name) down to a code we
                              can match against an appId.

Why a committed snapshot rather than a runtime fetch: the list changes a few
times a year, it is a few KB, and a settlement card must not wait on a
third-party request to render its pay button.

The upstream `autofill` flag is recorded but deliberately unused — see
`src/lib/deeplink.ts` for why it is not trustworthy enough to branch on.
"""
import json
import os
import urllib.request

ANDROID = "https://api.vietqr.io/v2/android-app-deeplinks"
IOS = "https://api.vietqr.io/v2/ios-app-deeplinks"
BANKS = "https://api.vietqr.io/v2/banks"

OUT = os.path.join(os.path.dirname(__file__), "..", "src", "lib", "bank-apps.json")


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


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
        })

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
        "_sources": [ANDROID, IOS, BANKS],
        "apps": apps,
        "banks": bank_rows,
    }

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    with_app = sum(1 for b in bank_rows if b["hasApp"])
    print(f"wrote {os.path.normpath(OUT)}")
    print(f"  {len(apps)} apps, {len(bank_rows)} banks ({with_app} with an app)")


if __name__ == "__main__":
    main()

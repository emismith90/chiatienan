# Privacy Notice — chiatienan

**Personal, non-commercial hobby project.** chiatienan is a small bot built and run by an
individual (Hung Le) to split lunch costs within a private group of friends/colleagues. It is a
**personal trial**, provided as-is, not a Niteco product or service.

## What it stores
- Group members' **display names** and, for generating payment QR codes, their **bank code,
  account number, and account holder name** (entered by the group's admin).
- **Meal records**: amount, who paid, who took part, and each person's share.

Data lives in a private SQLite database on a personal server. It is **not sold, and not shared**
with anyone outside the group.

## Third-party processing
When you send text or a **bill photo**, that content is sent to the **Cursor SDK / its LLM
provider** to interpret the amount and participants. Don't send anything you wouldn't want
processed by that service. Payment QR images are generated via a public **VietQR** image URL
(bank code, account number, and amount appear in that request).

## Your choices
Ask the admin to remove your member record and bank details at any time. Since the ledger is
append-only, historical meal entries may be retained but can be voided.

## Contact
Hung Le — via the group.

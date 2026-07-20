"""Password-protected ``/admin`` roster page (design D8).

Bank numbers are error-prone to type in chat, and nobody knows their own ``29:…``
Teams id — so the admin sets people up here and links the identities the bot has
captured on first mention. Auth is a single shared password (design's stated
scope: no RBAC) held in an HMAC-signed, HttpOnly cookie.
"""
from __future__ import annotations

import hashlib
import hmac
import html

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import roster
from app.config import settings
from app.db import get_db

router = APIRouter()

_COOKIE = "chiatienan_admin"


def _expected_token() -> str:
    key = settings.admin_password.encode()
    return hmac.new(key, b"chiatienan-admin-v1", hashlib.sha256).hexdigest()


def _is_authed(request: Request) -> bool:
    if not settings.admin_password:
        return False
    token = request.cookies.get(_COOKIE, "")
    return hmac.compare_digest(token, _expected_token())


def _page(inner: str) -> str:
    return (
        "<!doctype html><html lang='vi'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>chiatienan · admin</title>"
        "<style>"
        "body{font-family:system-ui,Segoe UI,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1b1b1b}"
        "h1{font-size:1.3rem} table{border-collapse:collapse;width:100%;font-size:.9rem}"
        "th,td{border:1px solid #ddd;padding:.4rem;text-align:left} th{background:#f5f5f5}"
        "input,select{padding:.3rem;font:inherit} form.row input{width:8rem}"
        ".btn{background:#4b3fbb;color:#fff;border:0;padding:.45rem .8rem;border-radius:6px;cursor:pointer}"
        ".muted{color:#777;font-size:.85rem} .stub{background:#fff8e1}"
        "</style></head><body>" + inner + "</body></html>"
    )


def _login_page(error: str = "") -> str:
    err = f"<p style='color:#c00'>{html.escape(error)}</p>" if error else ""
    return _page(
        "<h1>chiatienan · đăng nhập admin</h1>" + err +
        "<form method='post' action='/admin/login'>"
        "<input type='password' name='password' placeholder='Mật khẩu admin' autofocus> "
        "<button class='btn' type='submit'>Đăng nhập</button></form>"
    )


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _member_row(m) -> str:
    aliases = ", ".join(m.aliases or [])
    cls = " class='stub'" if not m.active else ""
    checked = "checked" if m.active else ""
    return (
        f"<tr{cls}><form method='post' action='/admin/member'>"
        f"<td>{m.id}<input type='hidden' name='member_id' value='{m.id}'></td>"
        f"<td><input name='display_name' value='{_esc(m.display_name)}'></td>"
        f"<td><input name='aliases' value='{_esc(aliases)}'></td>"
        f"<td class='muted'>{_esc(m.teams_user_id)}</td>"
        f"<td><input name='bank_code' value='{_esc(m.bank_code)}' style='width:5rem'></td>"
        f"<td><input name='account_number' value='{_esc(m.account_number)}'></td>"
        f"<td><input name='account_holder' value='{_esc(m.account_holder)}'></td>"
        f"<td><input type='checkbox' name='active' {checked}></td>"
        f"<td><button class='btn' type='submit'>Lưu</button></td>"
        f"</form></tr>"
    )


def _roster_page(request: Request) -> str:
    with get_db().session() as s:
        members = roster.list_members(s)
        rows = "".join(_member_row(m) for m in members)
    new_row = (
        "<tr><form method='post' action='/admin/member'>"
        "<td>mới</td>"
        "<td><input name='display_name' placeholder='Tên'></td>"
        "<td><input name='aliases' placeholder='biệt danh, cách nhau bởi phẩy'></td>"
        "<td class='muted'>—</td>"
        "<td><input name='bank_code' placeholder='VCB' style='width:5rem'></td>"
        "<td><input name='account_number' placeholder='số TK'></td>"
        "<td><input name='account_holder' placeholder='CHỦ TK'></td>"
        "<td><input type='checkbox' name='active' checked></td>"
        "<td><button class='btn' type='submit'>Thêm</button></td>"
        "</form></tr>"
    )
    return _page(
        "<h1>chiatienan · quản lý thành viên</h1>"
        "<p class='muted'>Dòng nền vàng = thành viên bot mới ghi nhận (chưa kích hoạt). "
        "Điền thông tin ngân hàng và tick 'active' để dùng.</p>"
        "<table><tr><th>id</th><th>Tên</th><th>Biệt danh</th><th>Teams id</th>"
        "<th>Bank</th><th>Số TK</th><th>Chủ TK</th><th>Active</th><th></th></tr>"
        + rows + new_row + "</table>"
        "<p style='margin-top:1rem'><a href='/admin/logout'>Đăng xuất</a></p>"
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request):
    if not settings.admin_password:
        return HTMLResponse("Admin chưa được cấu hình (thiếu ADMIN_PASSWORD).", status_code=503)
    if not _is_authed(request):
        return HTMLResponse(_login_page())
    return HTMLResponse(_roster_page(request))


@router.post("/admin/login")
async def admin_login(password: str = Form("")):
    if not settings.admin_password:
        return HTMLResponse("Admin chưa được cấu hình.", status_code=503)
    if not hmac.compare_digest(password, settings.admin_password):
        return HTMLResponse(_login_page("Sai mật khẩu."), status_code=401)
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(_COOKIE, _expected_token(), httponly=True, samesite="lax", secure=True)
    return resp


@router.get("/admin/logout")
async def admin_logout():
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(_COOKIE)
    return resp


@router.post("/admin/member")
async def admin_member(
    request: Request,
    member_id: str = Form(""),
    display_name: str = Form(""),
    aliases: str = Form(""),
    bank_code: str = Form(""),
    account_number: str = Form(""),
    account_holder: str = Form(""),
    active: str = Form(""),
):
    if not _is_authed(request):
        return RedirectResponse("/admin", status_code=303)

    alias_list = [a.strip() for a in aliases.split(",") if a.strip()]
    is_active = bool(active)
    fields = dict(
        display_name=display_name.strip(),
        aliases=alias_list,
        bank_code=bank_code.strip() or None,
        account_number=account_number.strip() or None,
        account_holder=account_holder.strip() or None,
        active=is_active,
    )
    with get_db().session() as s:
        if member_id.strip().isdigit():
            roster.update_member(s, int(member_id), **fields)
        elif display_name.strip():
            roster.create_member(s, **fields)
    return RedirectResponse("/admin", status_code=303)

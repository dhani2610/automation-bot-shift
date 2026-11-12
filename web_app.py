"""FastAPI Web Dashboard for Locum ED HJ Auto-Booking (PC & Mobile Responsive)."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import db
from auto_booking import DAY_NAMES_ID, MYT, clean_digits, collect_unique_slots
from bot_manager import MANAGER, WIB, compute_run_datetime, record_outcome_and_notify, to_myt_naive
from locum_api import ApiError, LocumClient
from notifier import send_telegram_message

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Locum ED HJ Auto-Booking Dashboard")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.on_event("startup")
def on_startup() -> None:
    try:
        db.init_db()
        MANAGER.resume_active_jobs()
        MANAGER.start_watchdog(60.0)
    except db.DatabaseError as exc:
        print(f"[WARNING] MySQL belum terhubung saat startup: {exc}")


class LoginRequest(BaseModel):
    ic: str
    last4: str


class DirectBookRequest(BaseModel):
    ic: str
    date: str
    slot: str


class CancelBookRequest(BaseModel):
    ic: str
    booking_id: str


class TelegramConfigRequest(BaseModel):
    ic: str
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = True
    notify_on_success: bool = True
    notify_on_fail: bool = True
    notify_on_dry_run: bool = False


class TelegramTestRequest(BaseModel):
    ic: str
    bot_token: str = ""
    chat_id: str = ""


class CreateJobRequest(BaseModel):
    ic: str
    title: str = ""
    mode: str = Field(pattern="^(scheduled|monitor)$")
    target_dates: list[str] = Field(default_factory=list)
    target_slots: list[str] = Field(default_factory=list)
    schedule_time: str = "00:00"
    schedule_tz: str = Field(default="MYT", pattern="^(MYT|WIB)$")
    schedule_date: str | None = None
    poll_interval: float = 2.0
    max_bookings: int = 1
    one_per_day: bool = False
    dry_run: bool = False
    start_now: bool = True


def _get_authed_client(ic: str) -> tuple[LocumClient, dict[str, Any]]:
    ic_clean = clean_digits(ic)
    if len(ic_clean) != 12:
        raise HTTPException(status_code=400, detail="IC harus 12 digit angka.")
    try:
        acc = db.get_account(ic_clean)
    except db.DatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not acc:
        raise HTTPException(status_code=401, detail="Akun belum login di dashboard. Silakan login ulang.")
    client = LocumClient()
    try:
        client.login(ic_clean, acc["last4"])
    except ApiError as exc:
        raise HTTPException(status_code=401, detail=f"Login API gagal: {exc}") from exc
    return client, acc


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> RedirectResponse:
    target = "/jadwal" if request.cookies.get("locum_ic") else "/login"
    return RedirectResponse(url=target, status_code=302)


@app.get("/login", response_class=HTMLResponse)
def page_login(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"page_name": "login", "active_page": "login"})


@app.get("/jadwal", response_class=HTMLResponse)
def page_jadwal(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "jadwal.html", {"page_name": "jadwal", "active_page": "jadwal"})


@app.get("/auto-book", response_class=HTMLResponse)
def page_auto_book(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "auto_book.html", {"page_name": "auto_book", "active_page": "auto_book"})


@app.get("/riwayat", response_class=HTMLResponse)
def page_riwayat(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "riwayat.html", {"page_name": "riwayat", "active_page": "riwayat"})


@app.get("/logs", response_class=HTMLResponse)
def page_logs(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "logs.html", {"page_name": "logs", "active_page": "logs"})


@app.get("/telegram", response_class=HTMLResponse)
def page_telegram(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "telegram.html", {"page_name": "telegram", "active_page": "telegram"})


@app.get("/watchdog", response_class=HTMLResponse)
def page_watchdog(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "watchdog.html", {"page_name": "watchdog", "active_page": "watchdog"})


@app.get("/api/watchdog-status")
def api_watchdog_status(ic: str | None = Query(None)) -> dict[str, Any]:
    db_ok, db_msg = db.check_health()
    now_wib = dt.datetime.now(WIB)
    now_myt = dt.datetime.now(MYT)
    snap = MANAGER.watchdog_snapshot()
    live_ids = {w["job_id"] for w in snap["active_workers"] if w.get("alive")}

    active_jobs_out: list[dict[str, Any]] = []
    if db_ok:
        try:
            for j in db.list_active_jobs_all():
                if ic and j.get("account_ic") != clean_digits(ic):
                    continue
                active_jobs_out.append(
                    {
                        "id": j["id"],
                        "title": j["title"],
                        "status": j["status"],
                        "mode": j["mode"],
                        "schedule_date": j.get("schedule_date"),
                        "schedule_time": j.get("schedule_time"),
                        "schedule_tz": j.get("schedule_tz"),
                        "next_run_at": j.get("next_run_at"),
                        "last_message": j.get("last_message"),
                        "has_live_thread": int(j["id"]) in live_ids,
                    }
                )
        except db.DatabaseError:
            pass

    cron_log_path = Path("/var/log/locumsync-watchdog.log")
    cron_lines: list[str] = []
    if cron_log_path.exists():
        try:
            cron_lines = cron_log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]
            cron_lines.reverse()
        except OSError:
            cron_lines = []

    return {
        "ok": True,
        "pid": os.getpid(),
        "tz_env": os.environ.get("TZ", "system"),
        "server_time_myt": now_myt.strftime("%Y-%m-%d %H:%M:%S"),
        "server_time_wib": now_wib.strftime("%Y-%m-%d %H:%M:%S"),
        "db_ok": db_ok,
        "db_message": db_msg,
        "watchdog": snap,
        "active_db_jobs": active_jobs_out,
        "cron_log_path": str(cron_log_path),
        "cron_log_exists": cron_log_path.exists(),
        "cron_logs": cron_lines,
    }


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    db_ok, db_msg = db.check_health()
    now_wib = dt.datetime.now(WIB)
    now_myt = dt.datetime.now(MYT)
    next_midnight_myt = now_myt.replace(hour=0, minute=0, second=0, microsecond=0)
    if next_midnight_myt <= now_myt:
        next_midnight_myt += dt.timedelta(days=1)
    auto_new_date = (next_midnight_myt.date() + dt.timedelta(days=30)).isoformat()
    return {
        "ok": True,
        "db_ok": db_ok,
        "db_message": db_msg,
        "server_epoch_ms": int(now_myt.timestamp() * 1000),
        "server_time_wib": now_wib.strftime("%Y-%m-%d %H:%M:%S"),
        "server_time_myt": now_myt.strftime("%Y-%m-%d %H:%M:%S"),
        "auto_new_date": auto_new_date,
        "active_jobs": MANAGER.active_job_ids(),
    }


@app.get("/api/accounts")
def api_accounts() -> dict[str, Any]:
    try:
        return {"ok": True, "accounts": db.list_accounts()}
    except db.DatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/login")
def api_login(req: LoginRequest, response: Response) -> dict[str, Any]:
    ic = clean_digits(req.ic)
    last4 = clean_digits(req.last4)
    if len(ic) != 12:
        raise HTTPException(status_code=400, detail="IC number harus tepat 12 digit angka.")
    if len(last4) != 4:
        raise HTTPException(status_code=400, detail="4 digit terakhir no. telepon harus 4 digit.")

    client = LocumClient()
    try:
        if not client.check_ic(ic):
            raise HTTPException(
                status_code=404,
                detail="IC belum terdaftar di website Locum ED HJ. Daftar dulu lewat website utama.",
            )
        sess = client.login(ic, last4)
    except ApiError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        db.init_db()
        db.upsert_account(ic, last4, sess.profile)
        db.add_log(None, ic, "INFO", f"Login dashboard berhasil sebagai {sess.profile.get('name')}.")
    except db.DatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    response.set_cookie("locum_ic", ic, max_age=30 * 86400, samesite="lax")
    return {"ok": True, "profile": sess.profile}


@app.post("/api/logout")
def api_logout(response: Response) -> dict[str, Any]:
    response.delete_cookie("locum_ic")
    return {"ok": True}


@app.get("/api/availability")
def api_availability(ic: str = Query(...)) -> dict[str, Any]:
    client, _ = _get_authed_client(ic)
    try:
        days = client.get_availability()
        bookings, cutoff = client.get_my_bookings()
    except ApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    role_key = client.session.role_key if client.session else "mo"
    booked_keys = {(b["date"], b["slot"]) for b in bookings}
    unique_slots = [{"key": k, "label": lbl} for k, lbl in collect_unique_slots(days)]

    enriched_days: list[dict[str, Any]] = []
    for day in days:
        d_str = day["date"]
        try:
            d_name = DAY_NAMES_ID[dt.date.fromisoformat(d_str).weekday()]
        except ValueError:
            d_name = "-"
        slots_out = []
        open_count = 0
        booked_count = 0
        for s in day.get("slots") or []:
            key = s["key"]
            is_mine = (d_str, key) in booked_keys
            role_info = s.get(role_key) or {}
            left = int(role_info.get("left") or 0)
            total = int(role_info.get("total") or 0)
            if s.get("closed"):
                st = "closed"
            elif is_mine:
                st = "booked"
                booked_count += 1
            elif left > 0:
                st = "open"
                open_count += 1
            else:
                st = "full"
            slots_out.append(
                {
                    "key": key,
                    "label": s.get("label", key),
                    "status": st,
                    "left": left,
                    "total": total,
                    "mo": s.get("mo"),
                    "amo": s.get("amo"),
                    "sn": s.get("sn"),
                }
            )
        enriched_days.append(
            {
                "date": d_str,
                "day_name": d_name,
                "holiday": bool(day.get("holiday")),
                "open_count": open_count,
                "booked_count": booked_count,
                "slots": slots_out,
            }
        )

    return {
        "ok": True,
        "role_key": role_key,
        "days": enriched_days,
        "unique_slots": unique_slots,
        "bookings": bookings,
        "cutoff": cutoff,
    }


@app.post("/api/book-direct")
def api_book_direct(req: DirectBookRequest) -> dict[str, Any]:
    client, acc = _get_authed_client(req.ic)
    try:
        booking = client.book_slot(req.date, req.slot)
    except ApiError as exc:
        record_outcome_and_notify(
            job_id=None,
            account=acc,
            job_title="Booking Langsung (Dashboard)",
            mode="direct",
            outcome="FAILED",
            slot_date=req.date,
            slot_key=req.slot,
            slot_label=req.slot,
            reason=str(exc),
            is_dry_run=False,
        )
        try:
            db.add_log(None, req.ic, "WARN", f"Direct book gagal ({req.date} {req.slot}): {exc}")
        except db.DatabaseError:
            pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ref_id = booking.get("id", "-")
    seat = booking.get("seat", "-")
    label = booking.get("label", req.slot)
    record_outcome_and_notify(
        job_id=None,
        account=acc,
        job_title="Booking Langsung (Dashboard)",
        mode="direct",
        outcome="SUCCESS",
        slot_date=req.date,
        slot_key=req.slot,
        slot_label=label,
        seat=seat,
        booking_ref=ref_id,
        reason="Booking langsung berhasil dikonfirmasi oleh server Locum ED HJ.",
        is_dry_run=False,
    )
    try:
        db.add_log(None, req.ic, "SUCCESS", f"Direct booking BERHASIL: {req.date} ({label}) | Seat {seat} | Ref {ref_id}")
    except db.DatabaseError:
        pass
    return {"ok": True, "booking": booking}


@app.post("/api/cancel-booking")
def api_cancel_booking(req: CancelBookRequest) -> dict[str, Any]:
    client, _ = _get_authed_client(req.ic)
    try:
        client.cancel_booking(req.booking_id)
    except ApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        db.add_log(None, req.ic, "WARN", f"Booking Ref {req.booking_id} dicancel lewat dashboard.")
    except db.DatabaseError:
        pass
    return {"ok": True}


def _enrich_job(j: dict[str, Any]) -> dict[str, Any]:
    j["is_thread_alive"] = MANAGER.is_active(int(j["id"]))
    nra = j.get("next_run_at")
    if nra:
        j["next_run_iso"] = nra.replace(" ", "T") + "+08:00"
        j["next_run_myt"] = f"{nra} MYT"
    else:
        j["next_run_iso"] = None
        j["next_run_myt"] = None
    return j


@app.get("/api/jobs")
def api_list_jobs(ic: str = Query(...)) -> dict[str, Any]:
    ic_clean = clean_digits(ic)
    try:
        jobs = db.list_jobs(ic_clean)
        results = db.list_booking_results(ic_clean, limit=100)
    except db.DatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    for j in jobs:
        _enrich_job(j)
    summary = {
        "total": len(results),
        "success": sum(1 for r in results if r.get("outcome") == "SUCCESS"),
        "failed": sum(1 for r in results if r.get("outcome") in {"FAILED", "NO_SLOT"}),
        "dry_run": sum(1 for r in results if r.get("outcome") == "DRY_RUN"),
    }
    return {"ok": True, "jobs": jobs, "results": results, "summary": summary}


@app.get("/api/history")
def api_history(
    ic: str = Query(...),
    outcome: str = Query(default="ALL"),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    ic_clean = clean_digits(ic)
    try:
        all_rows = db.list_booking_results(ic_clean, outcome="ALL", limit=limit)
        filtered = db.list_booking_results(ic_clean, outcome=outcome, limit=limit) if outcome != "ALL" else all_rows
    except db.DatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    summary = {
        "total": len(all_rows),
        "success": sum(1 for r in all_rows if r.get("outcome") == "SUCCESS"),
        "failed": sum(1 for r in all_rows if r.get("outcome") in {"FAILED", "NO_SLOT"}),
        "dry_run": sum(1 for r in all_rows if r.get("outcome") == "DRY_RUN"),
    }
    return {"ok": True, "results": filtered, "summary": summary}


@app.get("/api/telegram")
def api_get_telegram(ic: str = Query(...)) -> dict[str, Any]:
    ic_clean = clean_digits(ic)
    try:
        cfg = db.get_telegram_settings(ic_clean)
    except db.DatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, "settings": cfg}


@app.post("/api/telegram")
def api_save_telegram(req: TelegramConfigRequest) -> dict[str, Any]:
    ic_clean = clean_digits(req.ic)
    try:
        cfg = db.upsert_telegram_settings(
            ic_clean,
            req.bot_token,
            req.chat_id,
            req.enabled,
            req.notify_on_success,
            req.notify_on_fail,
            req.notify_on_dry_run,
        )
        db.add_log(None, ic_clean, "INFO", "Pengaturan notifikasi Bot Telegram disimpan ke MySQL.")
    except db.DatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, "settings": cfg}


@app.post("/api/telegram/test")
def api_test_telegram(req: TelegramTestRequest) -> dict[str, Any]:
    ic_clean = clean_digits(req.ic)
    token = req.bot_token.strip()
    chat_id = req.chat_id.strip()
    if not token or not chat_id:
        try:
            saved = db.get_telegram_settings(ic_clean)
            token = token or saved.get("bot_token", "")
            chat_id = chat_id or saved.get("chat_id", "")
        except db.DatabaseError:
            pass
    ok, info = send_telegram_message(
        token,
        chat_id,
        "✅ <b>Tes Notifikasi Locum ED HJ Bot</b>\nKoneksi Bot Telegram berhasil! Notifikasi booking akan masuk ke chat ini.",
    )
    try:
        db.add_log(None, ic_clean, "SUCCESS" if ok else "ERROR", f"Tes Telegram: {info}")
    except db.DatabaseError:
        pass
    if not ok:
        raise HTTPException(status_code=400, detail=info)
    return {"ok": True, "message": info}


@app.post("/api/jobs")
def api_create_job(req: CreateJobRequest) -> dict[str, Any]:
    ic = clean_digits(req.ic)
    try:
        acc = db.get_account(ic)
    except db.DatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not acc:
        raise HTTPException(status_code=401, detail="Akun belum login.")

    norm_time = req.schedule_time.strip()
    if len(norm_time) == 5:
        norm_time = f"{norm_time}:00"
    if req.mode == "scheduled":
        parts = norm_time.split(":")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise HTTPException(status_code=400, detail="Format jam harus HH:MM (mis. 00:00).")
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
            raise HTTPException(status_code=400, detail="Jam/menit tidak valid.")

    next_run_str = None
    if req.mode == "scheduled":
        run_dt = compute_run_datetime(norm_time, req.schedule_tz, req.schedule_date or None)
        next_run_str = to_myt_naive(run_dt).strftime("%Y-%m-%d %H:%M:%S")

    auto_title = req.title.strip()
    if not auto_title:
        if req.mode == "scheduled":
            auto_title = f"Terjadwal {norm_time[:5]} {req.schedule_tz}"
        else:
            auto_title = "Pantau Batalan"
        if req.target_dates:
            auto_title += f" ({len(req.target_dates)} tgl)"

    job = db.create_job(
        {
            "account_ic": ic,
            "title": auto_title,
            "mode": req.mode,
            "target_dates": req.target_dates,
            "target_slots": req.target_slots,
            "schedule_time": norm_time if req.mode == "scheduled" else None,
            "schedule_tz": req.schedule_tz,
            "schedule_date": req.schedule_date or None,
            "next_run_at": next_run_str,
            "poll_interval": max(0.5, req.poll_interval),
            "max_bookings": max(0, req.max_bookings),
            "one_per_day": req.one_per_day,
            "dry_run": req.dry_run,
            "status": "pending",
            "last_message": "Job tersimpan di database.",
        }
    )
    db.add_log(int(job["id"]), ic, "INFO", f"Job '{auto_title}' disimpan ke MySQL.")

    if req.start_now:
        MANAGER.start_job(int(job["id"]))
        job = db.get_job(int(job["id"])) or job

    return {"ok": True, "job": _enrich_job(job)}


@app.post("/api/jobs/{job_id}/start")
def api_start_job(job_id: int, ic: str = Query(...)) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job or job["account_ic"] != clean_digits(ic):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    started = MANAGER.start_job(job_id)
    if not started:
        raise HTTPException(status_code=409, detail="Job sudah sedang berjalan.")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/stop")
def api_stop_job(job_id: int, ic: str = Query(...)) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job or job["account_ic"] != clean_digits(ic):
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    MANAGER.stop_job(job_id)
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def api_delete_job(job_id: int, ic: str = Query(...)) -> dict[str, Any]:
    ic_clean = clean_digits(ic)
    job = db.get_job(job_id)
    if not job or job["account_ic"] != ic_clean:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    if MANAGER.is_active(job_id):
        MANAGER.stop_job(job_id)
    db.delete_job(job_id, ic_clean)
    return {"ok": True}


@app.get("/api/logs")
def api_logs(
    ic: str = Query(...),
    job_id: int | None = Query(default=None),
    level: str = Query(default="ALL"),
    limit: int = Query(default=120, ge=1, le=500),
) -> dict[str, Any]:
    try:
        logs = db.list_logs(clean_digits(ic), job_id=job_id, level=level, limit=limit)
    except db.DatabaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, "logs": logs}


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    uvicorn.run("web_app:app", host=host, port=port, reload=False)


"""Background job runner for scheduled and monitor auto-booking jobs."""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from typing import Any

import db
from auto_booking import DEFAULT_SLOTS, MYT, find_matching_slots
from locum_api import ApiError, LocumClient
from notifier import format_booking_notification, send_telegram_message

SLOT_LABEL_MAP = dict(DEFAULT_SLOTS)

WIB = dt.timezone(dt.timedelta(hours=7), name="WIB")


def get_tz(tz_name: str) -> dt.timezone:
    return MYT if (tz_name or "MYT").upper() == "MYT" else WIB


def compute_run_datetime(
    schedule_time: str,
    schedule_tz: str,
    schedule_date: str | None = None,
) -> dt.datetime:
    """Compute target run datetime in the requested timezone (MYT or WIB)."""
    tz = get_tz(schedule_tz)
    parts = [int(p) for p in (schedule_time or "00:00:00").split(":")]
    hour = parts[0]
    minute = parts[1] if len(parts) > 1 else 0
    second = parts[2] if len(parts) > 2 else 0
    now_tz = dt.datetime.now(tz)

    if schedule_date:
        d = dt.date.fromisoformat(schedule_date)
        return dt.datetime(d.year, d.month, d.day, hour, minute, second, tzinfo=tz)

    target = now_tz.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if target <= now_tz:
        target += dt.timedelta(days=1)
    return target


def to_local_naive(dt_aware: dt.datetime) -> dt.datetime:
    """Convert an aware datetime to local naive datetime."""
    return dt_aware.astimezone().replace(tzinfo=None)


def to_myt_naive(dt_aware: dt.datetime) -> dt.datetime:
    """Convert an aware datetime to Malaysia Time (MYT / UTC+8) naive datetime."""
    return dt_aware.astimezone(MYT).replace(tzinfo=None)


def record_outcome_and_notify(
    *,
    job_id: int | None,
    account: dict[str, Any],
    job_title: str,
    mode: str,
    outcome: str,
    slot_date: str,
    slot_key: str,
    slot_label: str | None = None,
    seat: str | None = None,
    booking_ref: str | None = None,
    reason: str | None = None,
    is_dry_run: bool = False,
) -> int | None:
    """Persist booking outcome (SUCCESS/FAILED/NO_SLOT/DRY_RUN) and notify Telegram if enabled."""
    ic = account.get("ic_number") or account.get("ic") or ""
    acc_name = account.get("name") or ic
    acc_role = account.get("role") or "MO"
    workplace = account.get("workplace") or "-"

    result_id: int | None = None
    try:
        result_id = db.add_booking_result(
            job_id=job_id,
            account_ic=ic,
            account_name=acc_name,
            account_role=acc_role,
            workplace=workplace,
            job_title=job_title,
            mode=mode,
            outcome=outcome,
            booking_ref=booking_ref,
            slot_date=slot_date,
            slot_key=slot_key,
            slot_label=slot_label,
            seat=seat,
            reason=reason,
            is_dry_run=is_dry_run,
        )
    except db.DatabaseError:
        result_id = None

    try:
        tg = db.get_telegram_settings(ic)
    except db.DatabaseError:
        return result_id

    should_send = False
    if tg.get("enabled") and tg.get("bot_token") and tg.get("chat_id"):
        if outcome == "SUCCESS" and tg.get("notify_on_success"):
            should_send = True
        elif outcome in {"FAILED", "NO_SLOT"} and tg.get("notify_on_fail"):
            should_send = True
        elif outcome == "DRY_RUN" and tg.get("notify_on_dry_run"):
            should_send = True

    if not should_send:
        if result_id is not None:
            try:
                db.update_booking_result_telegram(result_id, False, "Notifikasi Telegram tidak aktif untuk status ini.")
            except db.DatabaseError:
                pass
        return result_id

    text = format_booking_notification(
        outcome=outcome,
        account_name=acc_name,
        ic_number=ic,
        role=acc_role,
        workplace=workplace,
        slot_date=slot_date,
        slot_label=slot_label or slot_key,
        slot_key=slot_key,
        seat=seat,
        booking_ref=booking_ref,
        job_title=job_title,
        reason=reason,
    )
    ok, info = send_telegram_message(tg["bot_token"], tg["chat_id"], text)
    if result_id is not None:
        try:
            db.update_booking_result_telegram(result_id, ok, info)
            db.add_log(
                job_id,
                ic,
                "INFO" if ok else "WARN",
                f"Notifikasi Telegram ({outcome}): {info}",
            )
        except db.DatabaseError:
            pass
    return result_id


class BotManager:
    """Manages active background booking threads keyed by job_id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._workers: dict[int, tuple[threading.Thread, threading.Event]] = {}
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_interval: float = 60.0
        self._watchdog_checks: int = 0
        self._watchdog_last_myt: str | None = None
        self._watchdog_last_wib: str | None = None
        self._watchdog_last_resumed: list[int] = []

    def is_active(self, job_id: int) -> bool:
        with self._lock:
            item = self._workers.get(job_id)
            return bool(item and item[0].is_alive())

    def active_job_ids(self) -> list[int]:
        with self._lock:
            return [jid for jid, (th, _) in self._workers.items() if th.is_alive()]

    def start_job(self, job_id: int) -> bool:
        job = db.get_job(job_id)
        if not job:
            return False
        with self._lock:
            existing = self._workers.get(job_id)
            if existing and existing[0].is_alive():
                return False
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._run_job_safe,
                args=(job_id, stop_event),
                daemon=True,
                name=f"locum-job-{job_id}",
            )
            self._workers[job_id] = (thread, stop_event)
            thread.start()
            return True

    def stop_job(self, job_id: int) -> bool:
        with self._lock:
            item = self._workers.get(job_id)
            if not item:
                db.update_job_status(job_id, "stopped", "Dihentikan oleh user.", clear_next_run=True)
                return False
            thread, stop_event = item
            stop_event.set()
            alive = thread.is_alive()
        if not alive:
            db.update_job_status(job_id, "stopped", "Dihentikan oleh user.", clear_next_run=True)
        return True

    def resume_active_jobs(self) -> list[int]:
        """Auto-resume jobs that were waiting or running before a server restart."""
        resumed: list[int] = []
        try:
            jobs = db.list_active_jobs_all()
        except db.DatabaseError:
            return resumed
        for job in jobs:
            jid = int(job["id"])
            if self.start_job(jid):
                resumed.append(jid)
                self._safe_log(
                    jid,
                    job["account_ic"],
                    "INFO",
                    "Bot dilanjutkan otomatis saat server start / watchdog.",
                )
        return resumed

    def start_watchdog(self, interval_seconds: float = 60.0) -> None:
        """Periodically verify every waiting/running job in MySQL has a live worker thread."""
        with self._lock:
            if self._watchdog_thread and self._watchdog_thread.is_alive():
                return
            self._watchdog_stop.clear()
            self._watchdog_interval = interval_seconds

            def _record_tick(resumed_ids: list[int]) -> None:
                now_myt = dt.datetime.now(MYT)
                now_wib = now_myt.astimezone(WIB)
                self._watchdog_checks += 1
                self._watchdog_last_myt = now_myt.strftime("%Y-%m-%d %H:%M:%S")
                self._watchdog_last_wib = now_wib.strftime("%Y-%m-%d %H:%M:%S")
                self._watchdog_last_resumed = resumed_ids

            _record_tick([])

            def _loop() -> None:
                while not self._watchdog_stop.wait(interval_seconds):
                    try:
                        resumed_ids = self.resume_active_jobs()
                        _record_tick(resumed_ids)
                    except Exception:
                        _record_tick([])

            self._watchdog_thread = threading.Thread(
                target=_loop,
                daemon=True,
                name="bot-watchdog",
            )
            self._watchdog_thread.start()

    def watchdog_snapshot(self) -> dict[str, Any]:
        """Return live state of the in-process 60s watchdog and active worker threads."""
        with self._lock:
            wd_alive = bool(self._watchdog_thread and self._watchdog_thread.is_alive())
            workers = [
                {
                    "job_id": jid,
                    "thread_name": th.name,
                    "alive": th.is_alive(),
                }
                for jid, (th, _) in sorted(self._workers.items())
            ]
        return {
            "watchdog_alive": wd_alive,
            "interval_seconds": self._watchdog_interval,
            "checks_count": self._watchdog_checks,
            "last_check_myt": self._watchdog_last_myt,
            "last_check_wib": self._watchdog_last_wib,
            "last_resumed": list(self._watchdog_last_resumed),
            "active_workers": workers,
        }

    def _safe_log(
        self,
        job_id: int,
        account_ic: str,
        level: str,
        message: str,
        raw_payload: str | None = None,
    ) -> None:
        try:
            db.add_log(job_id, account_ic, level, message, raw_payload=raw_payload)
        except db.DatabaseError:
            pass

    def _safe_status(
        self,
        job_id: int,
        status: str,
        last_message: str | None = None,
        success_count: int | None = None,
        next_run_at: dt.datetime | None = None,
        clear_next_run: bool = False,
    ) -> None:
        try:
            db.update_job_status(
                job_id,
                status,
                last_message=last_message,
                success_count=success_count,
                next_run_at=next_run_at,
                clear_next_run=clear_next_run,
            )
        except db.DatabaseError:
            pass

    def _run_job_safe(self, job_id: int, stop_event: threading.Event) -> None:
        job = db.get_job(job_id)
        if not job:
            return
        ic = job["account_ic"]
        try:
            self._execute_job(job, stop_event)
        except Exception as exc:  # pragma: no cover - defensive guard
            msg = f"Error tak terduga pada bot ({type(exc).__name__}): {exc}"
            self._safe_log(job_id, ic, "ERROR", msg)
            self._safe_status(job_id, "error", msg, clear_next_run=True)
            account = db.get_account(ic) or {"ic_number": ic, "name": ic, "role": "MO", "workplace": "-"}
            record_outcome_and_notify(
                job_id=job_id,
                account=account,
                job_title=job.get("title") or f"Job #{job_id}",
                mode=job.get("mode") or "scheduled",
                outcome="FAILED",
                slot_date=", ".join(job.get("target_dates") or []) or "SEMUA TANGGAL",
                slot_key=", ".join(job.get("target_slots") or []) or "SEMUA JAM",
                slot_label=", ".join(job.get("target_slots") or []) or "SEMUA JAM",
                reason=msg,
                is_dry_run=bool(job.get("dry_run")),
            )
        finally:
            with self._lock:
                self._workers.pop(job_id, None)

    def _login_client(self, job_id: int, ic: str) -> LocumClient:
        acc = db.get_account(ic)
        if not acc:
            raise ApiError("Data akun tidak ditemukan di database.")
        client = LocumClient()
        client.login(ic, acc["last4"])
        self._safe_log(job_id, ic, "INFO", f"Login API berhasil sebagai {acc.get('name')} ({acc.get('role')}).")
        return client

    def _resolve_target_dates(self, raw_dates: list[str], target_dt: dt.datetime | None = None) -> set[str] | None:
        if not raw_dates:
            return None
        resolved: set[str] = set()
        for item in raw_dates:
            if item == "AUTO_30D":
                base_myt = (target_dt or dt.datetime.now(MYT)).astimezone(MYT).date()
                resolved.add((base_myt + dt.timedelta(days=30)).isoformat())
            else:
                resolved.add(item)
        return resolved or None

    def _execute_job(self, job: dict[str, Any], stop_event: threading.Event) -> None:
        job_id = int(job["id"])
        ic = job["account_ic"]
        mode = job["mode"]
        dry_run = bool(job["dry_run"])
        max_bookings = int(job["max_bookings"])
        one_per_day = bool(job["one_per_day"])
        poll_interval = max(0.3, float(job["poll_interval"] or 2.0))
        slot_filters = [s.lower() for s in (job.get("target_slots") or [])] or None

        account = db.get_account(ic) or {"ic_number": ic, "name": ic, "role": "MO", "workplace": "-"}
        job_title = job.get("title") or f"Job #{job_id}"

        try:
            client = self._login_client(job_id, ic)
            bookings, _ = client.get_my_bookings()
        except ApiError as exc:
            msg = f"Gagal login/sinkronisasi awal: {exc}"
            self._safe_log(job_id, ic, "ERROR", msg)
            self._safe_status(job_id, "error", msg, clear_next_run=True)
            record_outcome_and_notify(
                job_id=job_id,
                account=account,
                job_title=job_title,
                mode=mode,
                outcome="FAILED",
                slot_date=", ".join(job.get("target_dates") or []) or "SEMUA TANGGAL",
                slot_key=", ".join(slot_filters or []) or "SEMUA JAM",
                slot_label=", ".join(slot_filters or []) or "SEMUA JAM",
                reason=msg,
                is_dry_run=dry_run,
            )
            return
        booked_keys = {(b["date"], b["slot"]) for b in bookings}
        booked_dates = {b["date"] for b in bookings}
        role_key = client.session.role_key if client.session else "mo"

        target_run_dt: dt.datetime | None = None
        strike_pool: list[LocumClient] = []
        strike_targets = self._resolve_target_dates(job.get("target_dates") or [], None)
        has_direct_strike = bool(mode == "scheduled" and not dry_run and strike_targets and slot_filters)

        if mode == "scheduled":
            target_run_dt = compute_run_datetime(
                job.get("schedule_time") or "00:00:00",
                job.get("schedule_tz") or "MYT",
                job.get("schedule_date"),
            )
            strike_targets = self._resolve_target_dates(job.get("target_dates") or [], target_run_dt)
            has_direct_strike = bool(not dry_run and strike_targets and slot_filters)
            myt_naive = to_myt_naive(target_run_dt)
            wib_dt = target_run_dt.astimezone(WIB)
            wait_msg = (
                f"Menunggu jadwal {myt_naive.strftime('%Y-%m-%d %H:%M:%S')} MYT "
                f"(= {wib_dt.strftime('%H:%M:%S WIB')})"
            )
            self._safe_status(job_id, "waiting", wait_msg, next_run_at=myt_naive)
            self._safe_log(job_id, ic, "INFO", wait_msg)

            refreshed = False
            warmed = False
            wake_lead = 0.25 if has_direct_strike else 3.0
            while not stop_event.is_set():
                remaining = (target_run_dt - dt.datetime.now(target_run_dt.tzinfo)).total_seconds()
                if remaining <= wake_lead:
                    break
                if remaining <= 60.0 and not refreshed:
                    try:
                        acc = db.get_account(ic)
                        if acc:
                            client.login(ic, acc["last4"])
                            bookings, _ = client.get_my_bookings()
                            booked_keys = {(b["date"], b["slot"]) for b in bookings}
                            booked_dates = {b["date"] for b in bookings}
                            refreshed = True
                            self._safe_log(job_id, ic, "INFO", "Token sesi & data booking diperbarui 1 menit sebelum eksekusi.")
                    except ApiError as exc:
                        self._safe_log(job_id, ic, "WARN", f"Gagal refresh token: {exc}")
                if remaining <= 10.0 and not warmed:
                    warmed = True
                    if has_direct_strike:
                        strike_pool = [client]
                        for _ in range(5):
                            clone = LocumClient()
                            clone.session = client.session
                            clone._ic = client._ic
                            clone._last4 = client._last4
                            strike_pool.append(clone)

                        def _warm_one(c: LocumClient) -> None:
                            try:
                                c.get_availability()
                            except ApiError:
                                pass

                        warm_threads = [
                            threading.Thread(target=_warm_one, args=(c,), daemon=True)
                            for c in strike_pool
                        ]
                        for th in warm_threads:
                            th.start()
                        for th in warm_threads:
                            th.join(timeout=6.0)
                        self._safe_log(
                            job_id,
                            ic,
                            "INFO",
                            f"Koneksi {len(strike_pool)} session dipanaskan 10 detik sebelum mulai (Direct-Strike 0.1s aktif).",
                        )
                    else:
                        try:
                            client.get_availability()
                            self._safe_log(job_id, ic, "INFO", "Koneksi & cache endpoint dipanaskan 10 detik sebelum mulai.")
                        except ApiError:
                            pass
                stop_event.wait(min(1.0, max(0.02, remaining - wake_lead)))

            if stop_event.is_set():
                self._safe_status(job_id, "stopped", "Dihentikan saat menunggu jadwal.", clear_next_run=True)
                self._safe_log(job_id, ic, "WARN", "Job dihentikan oleh user sebelum jam mulai.")
                return

        target_dates = self._resolve_target_dates(job.get("target_dates") or [], target_run_dt)
        dates_label = ", ".join(sorted(target_dates)) if target_dates else "SEMUA TANGGAL"
        slots_label = " -> ".join(slot_filters) if slot_filters else "SEMUA JAM"

        self._safe_status(
            job_id,
            "running",
            f"Aktif memantau: {dates_label} ({slots_label})",
            clear_next_run=(mode == "monitor"),
        )
        self._safe_log(
            job_id,
            ic,
            "INFO",
            f"Bot MULAI berjalan ({mode.upper()}{', DRY-RUN' if dry_run else ''}) | "
            f"Tanggal: {dates_label} | Jam: {slots_label}",
        )

        success_count = int(job.get("success_count") or 0)
        run_booked = 0
        run_failed = 0
        checks = 0
        last_avail_error: str | None = None
        notify_threads: list[threading.Thread] = []
        snipe_end = (target_run_dt + dt.timedelta(seconds=120)) if mode == "scheduled" and target_run_dt else None
        burst_end = (target_run_dt + dt.timedelta(seconds=30)) if mode == "scheduled" and target_run_dt else None

        def notify_async(**kwargs: Any) -> None:
            th = threading.Thread(
                target=record_outcome_and_notify,
                kwargs=kwargs,
                daemon=True,
                name=f"tg-notify-{job_id}",
            )
            th.start()
            notify_threads.append(th)

        if has_direct_strike and target_run_dt is not None and strike_pool:
            strike_pairs = [
                (d, s)
                for d in sorted(target_dates or set())
                for s in (slot_filters or [])
                if (d, s) not in booked_keys and not (one_per_day and d in booked_dates)
            ]
            if strike_pairs:
                wave_offsets = (-0.10, 0.00, 0.10, 0.25, 0.50, 1.00)
                strike_lock = threading.Lock()
                strike_errors: list[str] = []

                def _fire_wave(wave_idx: int, offset_sec: float, date_str: str, slot_key: str) -> None:
                    nonlocal success_count, run_booked
                    fire_at = target_run_dt + dt.timedelta(seconds=offset_sec)
                    while not stop_event.is_set():
                        wait_s = (fire_at - dt.datetime.now(fire_at.tzinfo)).total_seconds()
                        if wait_s <= 0:
                            break
                        if wait_s > 0.03:
                            stop_event.wait(wait_s - 0.015)
                        else:
                            time.sleep(wait_s)
                            break
                    with strike_lock:
                        if stop_event.is_set() or (max_bookings > 0 and success_count >= max_bookings):
                            return
                        if (date_str, slot_key) in booked_keys or (one_per_day and date_str in booked_dates):
                            return
                    c = strike_pool[wave_idx % len(strike_pool)]
                    slot_label = SLOT_LABEL_MAP.get(slot_key, slot_key)
                    try:
                        booking = c.book_slot(date_str, slot_key)
                    except ApiError as exc:
                        with strike_lock:
                            strike_errors.append(str(exc))
                        return

                    ref_id = str(booking.get("id") or "-")
                    seat = str(booking.get("seat") or "-")
                    delta_ms = (dt.datetime.now(target_run_dt.tzinfo) - target_run_dt).total_seconds() * 1000.0
                    with strike_lock:
                        if max_bookings > 0 and success_count >= max_bookings:
                            return
                        success_count += 1
                        run_booked += 1
                        booked_keys.add((date_str, slot_key))
                        booked_dates.add(date_str)
                    notify_async(
                        job_id=job_id,
                        account=account,
                        job_title=job_title,
                        mode=mode,
                        outcome="SUCCESS",
                        slot_date=date_str,
                        slot_key=slot_key,
                        slot_label=slot_label,
                        seat=seat,
                        booking_ref=ref_id,
                        reason=f"Direct-Strike berhasil ({delta_ms:+.0f}ms dari jadwal).",
                        is_dry_run=False,
                    )
                    msg = f"BERHASIL BOOKING (Direct-Strike {delta_ms:+.0f}ms)! {date_str} | {slot_label} | Seat {seat} | Ref {ref_id}"
                    strike_snap = json.dumps({"strike": True, "delta_ms": round(delta_ms, 1), "date": date_str, "slot": slot_key, "booking": booking}, ensure_ascii=False)
                    self._safe_log(job_id, ic, "SUCCESS", msg, raw_payload=strike_snap)
                    self._safe_status(job_id, "running", msg, success_count=success_count)

                wave_threads: list[threading.Thread] = []
                w_idx = 0
                for offset_sec in wave_offsets:
                    for date_str, slot_key in strike_pairs:
                        th = threading.Thread(
                            target=_fire_wave,
                            args=(w_idx, offset_sec, date_str, slot_key),
                            daemon=True,
                            name=f"strike-{job_id}-{w_idx}",
                        )
                        th.start()
                        wave_threads.append(th)
                        w_idx += 1

                for th in wave_threads:
                    th.join(timeout=6.0)

                if max_bookings <= 0 or success_count < max_bookings:
                    last_err = strike_errors[-1] if strike_errors else "belum ada respons"
                    self._safe_log(
                        job_id,
                        ic,
                        "INFO",
                        f"Direct-Strike 6 gelombang selesai ({last_err}); lanjut rapid-polling availability...",
                    )

        while not stop_event.is_set():
            if max_bookings > 0 and success_count >= max_bookings:
                break
            if snipe_end is not None and dt.datetime.now(snipe_end.tzinfo) > snipe_end:
                self._safe_log(job_id, ic, "WARN", "Batas waktu rapid-polling (120 detik) selesai.")
                break

            checks += 1
            try:
                days = client.get_availability()
                last_avail_error = None
                matches = find_matching_slots(
                    days, role_key, booked_keys, booked_dates, target_dates, slot_filters, one_per_day
                )
                if matches:
                    for date_str, slot in matches:
                        if stop_event.is_set() or (max_bookings > 0 and success_count >= max_bookings):
                            break
                        slot_key = slot["key"]
                        slot_label = slot.get("label", slot_key)
                        if dry_run:
                            success_count += 1
                            run_booked += 1
                            booked_keys.add((date_str, slot_key))
                            booked_dates.add(date_str)
                            notify_async(
                                job_id=job_id,
                                account=account,
                                job_title=job_title,
                                mode=mode,
                                outcome="DRY_RUN",
                                slot_date=date_str,
                                slot_key=slot_key,
                                slot_label=slot_label,
                                seat="SIMULASI",
                                booking_ref=f"DRY-{checks}",
                                reason="Slot tersedia dan cocok dengan target (Simulasi Dry-Run).",
                                is_dry_run=True,
                            )
                            msg = f"[DRY-RUN] Cocok & simulasi booking: {date_str} | {slot_label}"
                            self._safe_log(job_id, ic, "SUCCESS", msg)
                            self._safe_status(job_id, "running", msg, success_count=success_count)
                            continue

                        self._safe_log(job_id, ic, "INFO", f"Slot OPEN ditemukan! Mencoba booking {date_str} ({slot_label})...")
                        try:
                            booking = client.book_slot(date_str, slot_key)
                            ref_id = str(booking.get("id") or "-")
                            seat = str(booking.get("seat") or "-")
                            success_count += 1
                            run_booked += 1
                            booked_keys.add((date_str, slot_key))
                            booked_dates.add(date_str)
                            notify_async(
                                job_id=job_id,
                                account=account,
                                job_title=job_title,
                                mode=mode,
                                outcome="SUCCESS",
                                slot_date=date_str,
                                slot_key=slot_key,
                                slot_label=slot_label,
                                seat=seat,
                                booking_ref=ref_id,
                                reason="Booking berhasil dikonfirmasi oleh server Locum ED HJ.",
                                is_dry_run=False,
                            )
                            msg = f"BERHASIL BOOKING! {date_str} | {slot_label} | Seat {seat} | Ref {ref_id}"
                            book_snap = json.dumps({"date": date_str, "slot": slot_key, "booking": booking}, ensure_ascii=False)
                            self._safe_log(job_id, ic, "SUCCESS", msg, raw_payload=book_snap)
                            self._safe_status(job_id, "running", msg, success_count=success_count)
                        except ApiError as book_exc:
                            run_failed += 1
                            notify_async(
                                job_id=job_id,
                                account=account,
                                job_title=job_title,
                                mode=mode,
                                outcome="FAILED",
                                slot_date=date_str,
                                slot_key=slot_key,
                                slot_label=slot_label,
                                reason=str(book_exc),
                                is_dry_run=False,
                            )
                            err_snap = json.dumps({"date": date_str, "slot": slot_key, "error": str(book_exc)}, ensure_ascii=False)
                            self._safe_log(job_id, ic, "WARN", f"Gagal booking {date_str} ({slot_key}): {book_exc}", raw_payload=err_snap)
                elif checks == 1 or checks % 10 == 0:
                    hb = f"Cek #{checks}: Belum ada slot kosong untuk target ({dates_label})."
                    target_day_data = None
                    if target_dates:
                        for d in days:
                            if d.get("date") in target_dates:
                                target_day_data = d
                                break
                    snap_dict = {
                        "check_num": checks,
                        "checked_at_myt": dt.datetime.now(MYT).strftime("%Y-%m-%d %H:%M:%S MYT"),
                        "target_dates": sorted(list(target_dates)) if target_dates else [],
                        "target_slots": slot_filters or [],
                        "target_day": target_day_data,
                        "total_days_returned": len(days),
                    }
                    raw_str = json.dumps(snap_dict, ensure_ascii=False)
                    self._safe_log(job_id, ic, "INFO", hb, raw_payload=raw_str)
                    self._safe_status(job_id, "running", hb)
            except ApiError as exc:
                last_avail_error = str(exc)
                err = f"Cek #{checks} gagal: {exc}"
                err_snap = json.dumps({"check_num": checks, "error": str(exc)}, ensure_ascii=False)
                self._safe_log(job_id, ic, "WARN", err, raw_payload=err_snap)
                self._safe_status(job_id, "running", err)

            if max_bookings > 0 and success_count >= max_bookings:
                break
            if mode == "scheduled":
                in_burst = burst_end is not None and dt.datetime.now(burst_end.tzinfo) <= burst_end
                wait_sec = 0.02 if in_burst else 0.25
            else:
                wait_sec = poll_interval
            stop_event.wait(wait_sec)

        for th in notify_threads:
            th.join(timeout=10.0)

        if stop_event.is_set():
            self._safe_status(job_id, "stopped", f"Dihentikan oleh user (total booking: {success_count}).", clear_next_run=True)
            self._safe_log(job_id, ic, "WARN", f"Bot dihentikan oleh user. Total berhasil: {success_count}.")
        else:
            if run_booked == 0 and run_failed == 0:
                no_slot_reason = f"Tidak ada slot kosong yang berhasil didapat setelah {checks} kali pengecekan."
                if last_avail_error:
                    no_slot_reason += f" Error API terakhir: {last_avail_error}"
                record_outcome_and_notify(
                    job_id=job_id,
                    account=account,
                    job_title=job_title,
                    mode=mode,
                    outcome="NO_SLOT",
                    slot_date=dates_label,
                    slot_key=slots_label,
                    slot_label=slots_label,
                    reason=no_slot_reason,
                    is_dry_run=dry_run,
                )
            done_msg = f"Selesai ({checks} cek). Total berhasil: {success_count}."
            self._safe_status(job_id, "completed", done_msg, success_count=success_count, clear_next_run=True)
            self._safe_log(job_id, ic, "INFO", done_msg)


MANAGER = BotManager()


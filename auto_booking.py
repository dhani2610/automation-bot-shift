"""CLI & interactive auto-booking bot for Locum ED HJ Booking."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import sys
import time
from typing import Any

from locum_api import ApiError, LocumClient

try:
    import winsound
except ImportError:  # Non-Windows fallback
    winsound = None

MYT = dt.timezone(dt.timedelta(hours=8), name="MYT")
LOG_FILE = "booking_log.txt"
DAY_NAMES_ID = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]
DEFAULT_SLOTS = [
    ("0900-1300", "9am – 1pm"),
    ("1400-1800", "2pm – 6pm"),
    ("1500-1900", "3pm – 7pm"),
    ("1800-2200", "6pm – 10pm"),
    ("1900-2300", "7pm – 11pm"),
]


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("locum_bot")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


LOGGER = setup_logging()


def log_and_print(msg: str) -> None:
    now_local = dt.datetime.now().strftime("%H:%M:%S")
    now_myt = dt.datetime.now(MYT).strftime("%H:%M:%S")
    print(f"[{now_local} | MYT {now_myt}] {msg}", flush=True)
    LOGGER.info(msg)


def beep_success() -> None:
    if winsound is None:
        print("\a", end="", flush=True)
        return
    try:
        winsound.Beep(880, 180)
        winsound.Beep(1175, 250)
    except RuntimeError:
        pass


def clean_digits(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def parse_single_date(token: str, ref_date: dt.date) -> dt.date:
    token = token.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
        return dt.date.fromisoformat(token)
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", token)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else ref_date.year
        if year < 100:
            year += 2000
        parsed = dt.date(year, month, day)
        if not m.group(3) and parsed < ref_date - dt.timedelta(days=30):
            parsed = dt.date(year + 1, month, day)
        return parsed
    raise ValueError(f"Format tanggal tidak valid: '{token}' (gunakan YYYY-MM-DD atau DD/MM)")


def parse_target_dates(raw: str | None) -> set[str] | None:
    """Return a set of YYYY-MM-DD strings, or None to match all dates."""
    if not raw or raw.strip().lower() in {"all", "*"}:
        return None
    ref = dt.datetime.now(MYT).date()
    result: set[str] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ".." in part:
            start_s, end_s = part.split("..", 1)
            start_d = parse_single_date(start_s, ref)
            end_d = parse_single_date(end_s, ref)
            if end_d < start_d:
                start_d, end_d = end_d, start_d
            cur = start_d
            while cur <= end_d:
                result.add(cur.isoformat())
                cur += dt.timedelta(days=1)
        else:
            result.add(parse_single_date(part, ref).isoformat())
    return result or None


def parse_target_slots(raw: str | None) -> list[str] | None:
    """Return lowercase slot filter tokens, or None to match all slots."""
    if not raw or raw.strip().lower() in {"all", "*"}:
        return None
    tokens = [t.strip().lower() for t in raw.split(",") if t.strip()]
    return tokens or None


def slot_priority(slot: dict[str, Any], slot_filters: list[str] | None) -> int:
    if not slot_filters:
        return 0
    haystack = f"{slot.get('key', '')} {slot.get('label', '')}".lower().replace("–", "-")
    for idx, tok in enumerate(slot_filters):
        if tok.replace("–", "-") in haystack:
            return idx
    return len(slot_filters)


def slot_matches(slot: dict[str, Any], slot_filters: list[str] | None) -> bool:
    if not slot_filters:
        return True
    haystack = f"{slot.get('key', '')} {slot.get('label', '')}".lower().replace("–", "-")
    return any(tok.replace("–", "-") in haystack for tok in slot_filters)


def find_matching_slots(
    days: list[dict[str, Any]],
    role_key: str,
    booked_keys: set[tuple[str, str]],
    booked_dates: set[str],
    target_dates: set[str] | None,
    slot_filters: list[str] | None,
    one_per_day: bool,
) -> list[tuple[str, dict[str, Any]]]:
    matches: list[tuple[int, int, str, dict[str, Any]]] = []
    for day_idx, day in enumerate(days):
        date_str = day.get("date", "")
        if target_dates is not None and date_str not in target_dates:
            continue
        if one_per_day and date_str in booked_dates:
            continue
        for slot in day.get("slots") or []:
            if slot.get("closed"):
                continue
            slot_key = slot.get("key", "")
            if (date_str, slot_key) in booked_keys:
                continue
            if not slot_matches(slot, slot_filters):
                continue
            left = int((slot.get(role_key) or {}).get("left") or 0)
            if left > 0:
                matches.append((day_idx, slot_priority(slot, slot_filters), date_str, slot))
    matches.sort(key=lambda item: (item[0], item[1]))
    return [(date_str, slot) for _, _, date_str, slot in matches]


def attempt_bookings(
    client: LocumClient,
    matches: list[tuple[str, dict[str, Any]]],
    booked_keys: set[tuple[str, str]],
    booked_dates: set[str],
    max_bookings: int,
    success_count: int,
    dry_run: bool,
) -> int:
    for date_str, slot in matches:
        if max_bookings > 0 and success_count >= max_bookings:
            break
        slot_key = slot["key"]
        label = slot.get("label", slot_key)
        if dry_run:
            log_and_print(f"[DRY-RUN] Slot cocok ditemukan: {date_str} | {label} ({slot_key})")
            booked_keys.add((date_str, slot_key))
            booked_dates.add(date_str)
            success_count += 1
            beep_success()
            continue

        log_and_print(f"Slot tersedia! Mencoba booking {date_str} | {label} ({slot_key})...")
        try:
            booking = client.book_slot(date_str, slot_key)
            ref_id = booking.get("id", "-")
            seat = booking.get("seat", "-")
            log_and_print(f"BERHASIL BOOKING! Tanggal: {date_str} | Jam: {label} | Seat: {seat} | Ref: {ref_id}")
            booked_keys.add((date_str, slot_key))
            booked_dates.add(date_str)
            success_count += 1
            beep_success()
        except ApiError as exc:
            log_and_print(f"Gagal booking {date_str} ({slot_key}): {exc}")
    return success_count


def format_day_name(date_str: str) -> str:
    try:
        return DAY_NAMES_ID[dt.date.fromisoformat(date_str).weekday()]
    except ValueError:
        return "---"


def parse_number_selection(raw: str, max_n: int) -> list[int] | None:
    """Parse '1,3,5-7' into ordered 1-based indices, or return None if not numeric indices."""
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not tokens:
        return None
    picked: list[int] = []
    for tok in tokens:
        if re.fullmatch(r"\d+", tok):
            num = int(tok)
            if not (1 <= num <= max_n):
                raise ValueError(f"Nomor {num} di luar daftar (1..{max_n}).")
            if num not in picked:
                picked.append(num)
        elif re.fullmatch(r"\d+-\d+", tok):
            a_s, b_s = tok.split("-", 1)
            a, b = int(a_s), int(b_s)
            if a > b:
                a, b = b, a
            for num in range(a, b + 1):
                if not (1 <= num <= max_n):
                    raise ValueError(f"Nomor {num} di luar daftar (1..{max_n}).")
                if num not in picked:
                    picked.append(num)
        else:
            return None
    return picked


def collect_unique_slots(days: list[dict[str, Any]]) -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for day in days:
        for s in day.get("slots") or []:
            key = s.get("key", "")
            if key and key not in seen:
                seen[key] = s.get("label", key)
    if not seen:
        return list(DEFAULT_SLOTS)
    return sorted(seen.items(), key=lambda x: x[0])


def print_availability(client: LocumClient, allow_direct_book: bool = False) -> None:
    role_key = client.session.role_key if client.session else "mo"
    days = client.get_availability()
    bookings, _ = client.get_my_bookings()
    booked_keys = {(b["date"], b["slot"]) for b in bookings}
    open_options: list[tuple[str, dict[str, Any]]] = []

    print("\n=== JADWAL & KETERSEDIAAN SLOT ===")
    print(f"Role Anda: {client.session.profile.get('role')} (kolom '{role_key.upper()}')\n")
    for idx, day in enumerate(days, start=1):
        d_str = day["date"]
        d_name = format_day_name(d_str)
        hol = "[PH]" if day.get("holiday") else "    "
        parts = []
        has_open = False
        for s in day.get("slots") or []:
            left = int((s.get(role_key) or {}).get("left") or 0)
            tot = int((s.get(role_key) or {}).get("total") or 0)
            mine_mark = "*" if (d_str, s["key"]) in booked_keys else ""
            if s.get("closed"):
                st = "CLOSED"
            elif mine_mark:
                st = "BOOKED*"
            elif left > 0:
                open_options.append((d_str, s))
                st = f"OPEN#{len(open_options)} {left}/{tot}"
                has_open = True
            else:
                st = f"FULL 0/{tot}"
            parts.append(f"{s['key']}({st})")
        flag = " <-- ADA SLOT!" if has_open else ""
        print(f"  [{idx:>2}] {d_str} ({d_name}) {hol} : " + " | ".join(parts) + flag)
    print("\nCatatan: * = sudah Anda booking.")

    if not allow_direct_book or not open_options:
        print()
        return

    print("\nSlot OPEN yang bisa langsung di-booking sekarang:")
    for i, (d_str, s) in enumerate(open_options, start=1):
        print(f"  [{i}] {d_str} ({format_day_name(d_str)}) | {s.get('label', s['key'])} ({s['key']})")
    raw = input("Ketik nomor untuk booking langsung (atau Enter untuk kembali): ").strip()
    if not raw:
        print()
        return
    if not raw.isdigit() or not (1 <= int(raw) <= len(open_options)):
        print("Pilihan tidak valid.\n")
        return
    d_str, s = open_options[int(raw) - 1]
    confirm = input(f"Konfirmasi booking {d_str} ({s.get('label', s['key'])})? [y/N]: ").strip().lower()
    if confirm == "y":
        booking = client.book_slot(d_str, s["key"])
        beep_success()
        log_and_print(
            f"BERHASIL BOOKING! Tanggal: {d_str} | Jam: {s.get('label', s['key'])} | "
            f"Seat: {booking.get('seat', '-')} | Ref: {booking.get('id', '-')}"
        )
    print()


def select_dates_interactive(
    days: list[dict[str, Any]],
    role_key: str,
    booked_keys: set[tuple[str, str]],
    default_new_date: str | None = None,
) -> set[str] | None:
    print("\n--- PILIH TARGET TANGGAL ---")
    for idx, day in enumerate(days, start=1):
        d_str = day["date"]
        d_name = format_day_name(d_str)
        hol = "[PH]" if day.get("holiday") else "    "
        slot_summary = []
        for s in day.get("slots") or []:
            key = s["key"]
            if (d_str, key) in booked_keys:
                tag = "BOOKED*"
            elif s.get("closed"):
                tag = "CLOSED"
            else:
                left = int((s.get(role_key) or {}).get("left") or 0)
                tag = f"OPEN({left})" if left > 0 else "FULL"
            slot_summary.append(f"{key}:{tag}")
        print(f"  [{idx:>2}] {d_str} ({d_name}) {hol} | " + " , ".join(slot_summary))

    if default_new_date:
        print(f"  [Enter] Otomatis tanggal baru yang dibuka: {default_new_date} ({format_day_name(default_new_date)})")
    else:
        print("  [Enter] Pantau SEMUA tanggal")

    while True:
        raw = input("Pilih nomor tanggal (mis. 3 atau 3,5 atau 10-15) / ketik DD/MM: ").strip()
        if not raw:
            return {default_new_date} if default_new_date else None
        try:
            nums = parse_number_selection(raw, len(days))
            if nums is not None:
                return {days[n - 1]["date"] for n in nums}
            return parse_target_dates(raw)
        except ValueError as exc:
            print(f"Input salah: {exc}")


def select_slots_interactive(days: list[dict[str, Any]]) -> list[str] | None:
    unique_slots = collect_unique_slots(days)
    print("\n--- PILIH TARGET JAM SHIFT (sesuai urutan prioritas) ---")
    for idx, (key, label) in enumerate(unique_slots, start=1):
        print(f"  [{idx}] {key} ({label})")
    print("  [Enter] Semua jam (ambil jam mana saja yang kosong)")

    while True:
        raw = input("Pilih nomor jam (mis. 4 atau 4,1 untuk prioritas): ").strip()
        if not raw:
            return None
        try:
            nums = parse_number_selection(raw, len(unique_slots))
            if nums is not None:
                return [unique_slots[n - 1][0].lower() for n in nums]
            return parse_target_slots(raw)
        except ValueError as exc:
            print(f"Input salah: {exc}")


def print_my_bookings(client: LocumClient, allow_cancel: bool = False) -> None:
    bookings, cutoff = client.get_my_bookings()
    print(f"\n=== BOOKING SAYA (Batas cancel gratis: {cutoff} jam sebelum slot) ===")
    if not bookings:
        print("  Belum ada booking aktif.\n")
        return
    for idx, b in enumerate(bookings, start=1):
        cancel_info = "Bisa cancel" if b.get("canCancel") else f"Terkunci (<{cutoff}j)"
        print(
            f"  [{idx}] {b['date']} | {b.get('label', b['slot'])} | "
            f"Seat: {b.get('seat', '-')} | Ref: {b['id']} | {cancel_info}"
        )
    print()
    if not allow_cancel:
        return
    choice = input("Ketik nomor untuk cancel booking (atau Enter untuk kembali): ").strip()
    if not choice:
        return
    if not choice.isdigit() or not (1 <= int(choice) <= len(bookings)):
        print("Pilihan tidak valid.\n")
        return
    target = bookings[int(choice) - 1]
    if not target.get("canCancel"):
        print("Booking ini sudah melewati batas cancel online.\n")
        return
    confirm = input(f"Yakin cancel {target['date']} ({target.get('label')}) Ref {target['id']}? [y/N]: ").strip().lower()
    if confirm == "y":
        client.cancel_booking(target["id"])
        log_and_print(f"Booking Ref {target['id']} ({target['date']}) berhasil dicancel.")


def run_monitor(
    client: LocumClient,
    target_dates: set[str] | None,
    slot_filters: list[str] | None,
    interval: float,
    max_bookings: int,
    one_per_day: bool,
    dry_run: bool,
) -> None:
    role_key = client.session.role_key
    bookings, _ = client.get_my_bookings()
    booked_keys = {(b["date"], b["slot"]) for b in bookings}
    booked_dates = {b["date"] for b in bookings}

    dates_desc = ", ".join(sorted(target_dates)) if target_dates else "SEMUA TANGGAL"
    slots_desc = ", ".join(slot_filters) if slot_filters else "SEMUA JAM"
    log_and_print(
        f"Mode MONITOR aktif | Target tanggal: {dates_desc} | Jam: {slots_desc} | "
        f"Interval: {interval}s | Max booking: {max_bookings or 'unlimited'}"
        + (" | [DRY-RUN]" if dry_run else "")
    )
    print("Tekan Ctrl+C untuk berhenti.\n")

    success_count = 0
    checks = 0
    while max_bookings == 0 or success_count < max_bookings:
        checks += 1
        try:
            days = client.get_availability()
            matches = find_matching_slots(
                days, role_key, booked_keys, booked_dates, target_dates, slot_filters, one_per_day
            )
            if matches:
                success_count = attempt_bookings(
                    client, matches, booked_keys, booked_dates, max_bookings, success_count, dry_run
                )
                if max_bookings > 0 and success_count >= max_bookings:
                    break
            elif checks % 10 == 1:
                log_and_print(f"Cek #{checks}: Belum ada slot kosong untuk target.")
        except ApiError as exc:
            log_and_print(f"Peringatan saat cek #{checks}: {exc}")
        time.sleep(max(0.3, interval))

    log_and_print(f"Monitor selesai. Total berhasil: {success_count}.")


def compute_next_myt_target(snipe_time: str) -> dt.datetime:
    parts = [int(x) for x in snipe_time.split(":")]
    hour = parts[0]
    minute = parts[1] if len(parts) > 1 else 0
    second = parts[2] if len(parts) > 2 else 0
    now_myt = dt.datetime.now(MYT)
    target = now_myt.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if target <= now_myt:
        target += dt.timedelta(days=1)
    return target


def run_sniper(
    client: LocumClient,
    ic: str,
    last4: str,
    target_dates: set[str] | None,
    slot_filters: list[str] | None,
    snipe_time: str,
    pre_seconds: float,
    snipe_interval: float,
    snipe_duration: float,
    max_bookings: int,
    one_per_day: bool,
    dry_run: bool,
) -> None:
    target_myt = compute_next_myt_target(snipe_time)
    target_local = target_myt.astimezone()
    if target_dates is None:
        # New day opens 30 days ahead of the new MYT date at 00:00 MYT
        opened_date = (target_myt.date() + dt.timedelta(days=30)).isoformat()
        target_dates = {opened_date}

    role_key = client.session.role_key
    bookings, _ = client.get_my_bookings()
    booked_keys = {(b["date"], b["slot"]) for b in bookings}
    booked_dates = {b["date"] for b in bookings}

    dates_desc = ", ".join(sorted(target_dates))
    slots_desc = ", ".join(slot_filters) if slot_filters else "SEMUA JAM"
    log_and_print(
        f"Mode SNIPER aktif | Drop: {target_myt.strftime('%Y-%m-%d %H:%M:%S MYT')} "
        f"(= {target_local.strftime('%Y-%m-%d %H:%M:%S Lokal')})"
    )
    log_and_print(
        f"Target tanggal: {dates_desc} | Jam: {slots_desc} | Max booking: {max_bookings or 'unlimited'}"
        + (" | [DRY-RUN]" if dry_run else "")
    )
    print("Tekan Ctrl+C untuk berhenti.\n")

    refreshed_token = False
    while True:
        remaining = (target_myt - dt.datetime.now(MYT)).total_seconds()
        if remaining <= pre_seconds:
            break
        if remaining <= 60 and not refreshed_token:
            try:
                client.login(ic, last4)
                refreshed_token = True
                print()
                log_and_print("Session token diperbarui sebelum drop.")
            except ApiError as exc:
                log_and_print(f"Gagal refresh token: {exc}")
        mins, secs = divmod(int(max(0, remaining)), 60)
        hrs, mins = divmod(mins, 60)
        print(f"\rMenunggu drop... sisa {hrs:02d}:{mins:02d}:{secs:02d}   ", end="", flush=True)
        time.sleep(min(1.0, max(0.05, remaining - pre_seconds)))

    print()
    log_and_print("Memulai rapid-polling sniper!")
    end_time = target_myt + dt.timedelta(seconds=snipe_duration)
    success_count = 0
    attempt = 0

    while dt.datetime.now(MYT) <= end_time:
        attempt += 1
        try:
            days = client.get_availability()
            matches = find_matching_slots(
                days, role_key, booked_keys, booked_dates, target_dates, slot_filters, one_per_day
            )
            if matches:
                success_count = attempt_bookings(
                    client, matches, booked_keys, booked_dates, max_bookings, success_count, dry_run
                )
                if max_bookings > 0 and success_count >= max_bookings:
                    break
        except ApiError as exc:
            log_and_print(f"Snipe #{attempt} error: {exc}")
        time.sleep(max(0.15, snipe_interval))

    log_and_print(f"Sniper selesai ({attempt} percobaan). Total berhasil: {success_count}.")


def prompt_credentials(ic_arg: str | None, last4_arg: str | None) -> tuple[str, str]:
    ic = clean_digits(ic_arg or "")
    while len(ic) != 12:
        ic = clean_digits(input("Masukkan IC number (12 digit, tanpa '-'): "))
        if len(ic) != 12:
            print("IC harus tepat 12 digit angka.")

    last4 = clean_digits(last4_arg or "")
    while len(last4) != 4:
        last4 = clean_digits(input("Masukkan 4 digit terakhir no. telepon: "))
        if len(last4) != 4:
            print("Harus tepat 4 digit angka.")
    return ic, last4


def login_with_feedback(client: LocumClient, ic: str, last4: str) -> bool:
    log_and_print(f"Mengecek IC {ic}...")
    if not client.check_ic(ic):
        print("IC belum terdaftar di website. Silakan daftar sekali lewat website terlebih dahulu.")
        return False
    sess = client.login(ic, last4)
    p = sess.profile
    log_and_print(
        f"Login berhasil: {p.get('name')} | Role: {p.get('role')} | Reg: {p.get('regNo')} | {p.get('workplace')}"
    )
    return True


def prompt_and_login(
    client: LocumClient, ic_arg: str | None = None, last4_arg: str | None = None
) -> tuple[str, str]:
    while True:
        ic, last4 = prompt_credentials(ic_arg, last4_arg)
        try:
            if login_with_feedback(client, ic, last4):
                return ic, last4
        except ApiError as exc:
            print(f"Login gagal: {exc}")
        if ic_arg or last4_arg:
            ic_arg = None
            last4_arg = None
        print("Silakan coba input ulang.\n")


def confirm_target_summary(
    mode_label: str,
    target_dates: set[str] | None,
    slot_filters: list[str] | None,
    extra: str = "",
) -> bool:
    dates_text = ", ".join(sorted(target_dates)) if target_dates else "SEMUA TANGGAL"
    slots_text = " -> ".join(slot_filters) if slot_filters else "SEMUA JAM"
    print("\n--- RINGKASAN TARGET ---")
    print(f"  Mode    : {mode_label}")
    print(f"  Tanggal : {dates_text}")
    print(f"  Jam     : {slots_text}")
    if extra:
        print(f"  Info    : {extra}")
    ans = input("Mulai sekarang? [Y/n]: ").strip().lower()
    return ans in {"", "y", "ya"}


def interactive_loop(client: LocumClient, ic: str, last4: str) -> None:
    while True:
        p = client.session.profile
        print(f"\n=== MENU AUTO BOOKING LOCUM ED HJ ({p.get('name')} - {p.get('role')}) ===")
        print("  [1] Cek Jadwal & Booking Langsung")
        print("  [2] Lihat Booking Saya (+ Cancel)")
        print("  [3] Auto-Book Monitor (Pilih jadwal & pantau slot kosong / batalan)")
        print("  [4] Midnight Sniper (Pilih jadwal & tembak tepat 00:00 MYT / 23:00 WIB)")
        print("  [5] Ganti Akun (Input IC & No. Telepon lain)")
        print("  [0] Keluar")
        choice = input("Pilih menu [1-5/0]: ").strip()

        if choice == "0":
            break
        elif choice == "1":
            print_availability(client, allow_direct_book=True)
        elif choice == "2":
            print_my_bookings(client, allow_cancel=True)
        elif choice == "3":
            days = client.get_availability()
            bookings, _ = client.get_my_bookings()
            booked_keys = {(b["date"], b["slot"]) for b in bookings}
            target_dates = select_dates_interactive(days, client.session.role_key, booked_keys)
            slot_filters = select_slots_interactive(days)
            raw_i = input("Interval cek dalam detik [default 3]: ").strip()
            raw_m = input("Max booking sebelum berhenti [default 1, 0=terus]: ").strip()
            one_day = input("Maksimal 1 shift per hari? [y/N]: ").strip().lower() == "y"
            dry = input("Mode simulasi / dry-run (tidak booking beneran)? [y/N]: ").strip().lower() == "y"
            interval = float(raw_i) if raw_i else 3.0
            max_b = int(raw_m) if raw_m else 1
            if not confirm_target_summary(
                "Auto-Book Monitor" + (" [DRY-RUN]" if dry else ""),
                target_dates,
                slot_filters,
                f"Cek tiap {interval}s | Max booking: {max_b or 'unlimited'}",
            ):
                continue
            try:
                run_monitor(client, target_dates, slot_filters, interval, max_b, one_day, dry)
            except KeyboardInterrupt:
                print("\nMonitor dihentikan. Kembali ke menu.\n")
        elif choice == "4":
            days = client.get_availability()
            bookings, _ = client.get_my_bookings()
            booked_keys = {(b["date"], b["slot"]) for b in bookings}
            next_mid = compute_next_myt_target("00:00:00")
            default_open = (next_mid.date() + dt.timedelta(days=30)).isoformat()
            target_dates = select_dates_interactive(
                days, client.session.role_key, booked_keys, default_new_date=default_open
            )
            slot_filters = select_slots_interactive(days)
            raw_t = input("Jam drop MYT [default 00:00:00]: ").strip() or "00:00:00"
            raw_m = input("Max booking sebelum berhenti [default 1]: ").strip()
            one_day = input("Maksimal 1 shift per hari? [y/N]: ").strip().lower() == "y"
            dry = input("Mode simulasi / dry-run (tidak booking beneran)? [y/N]: ").strip().lower() == "y"
            max_b = int(raw_m) if raw_m else 1
            if not confirm_target_summary(
                "Midnight Sniper" + (" [DRY-RUN]" if dry else ""),
                target_dates,
                slot_filters,
                f"Drop MYT: {raw_t} | Max booking: {max_b or 'unlimited'}",
            ):
                continue
            try:
                run_sniper(
                    client,
                    ic,
                    last4,
                    target_dates,
                    slot_filters,
                    raw_t,
                    2.0,
                    0.4,
                    60.0,
                    max_b,
                    one_day,
                    dry,
                )
            except KeyboardInterrupt:
                print("\nSniper dihentikan. Kembali ke menu.\n")
        elif choice == "5":
            print()
            ic, last4 = prompt_and_login(client)
        else:
            print("Pilihan tidak dikenal.\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Auto Booking CLI untuk Locum ED HJ")
    p.add_argument("--ic", help="IC number (12 digit tanpa '-')")
    p.add_argument("--last4", help="4 digit terakhir nomor telepon")
    p.add_argument("--mode", choices=["list", "mine", "monitor", "sniper"], help="Mode langsung tanpa menu")
    p.add_argument("--dates", help="Target tanggal (YYYY-MM-DD, DD/MM, range a..b, pisah koma, atau all)")
    p.add_argument("--slots", help="Filter jam slot (mis. 0900-1300,1800-2200 atau all)")
    p.add_argument("--interval", type=float, default=3.0, help="Interval polling mode monitor (detik)")
    p.add_argument("--max-bookings", type=int, default=1, help="Jumlah booking sebelum stop (0=unlimited)")
    p.add_argument("--one-per-day", action="store_true", help="Skip tanggal jika sudah ada booking di hari itu")
    p.add_argument("--snipe-time", default="00:00:00", help="Jam target sniper dalam MYT / UTC+8 (HH:MM:SS)")
    p.add_argument("--pre-seconds", type=float, default=2.0, help="Mulai rapid-poll X detik sebelum jam target")
    p.add_argument("--snipe-interval", type=float, default=0.4, help="Interval rapid-poll sniper (detik)")
    p.add_argument("--snipe-duration", type=float, default=60.0, help="Durasi sniper setelah jam target (detik)")
    p.add_argument("--dry-run", action="store_true", help="Simulasi saja tanpa mengirim request booking")
    return p


def main() -> int:
    args = build_parser().parse_args()
    client = LocumClient()
    try:
        if args.mode is None:
            ic, last4 = prompt_and_login(client, args.ic, args.last4)
            interactive_loop(client, ic, last4)
        else:
            ic, last4 = prompt_credentials(args.ic, args.last4)
            if not login_with_feedback(client, ic, last4):
                return 1
            if args.mode == "list":
                print_availability(client)
            elif args.mode == "mine":
                print_my_bookings(client, allow_cancel=False)
            elif args.mode == "monitor":
                run_monitor(
                    client,
                    parse_target_dates(args.dates),
                    parse_target_slots(args.slots),
                    args.interval,
                    args.max_bookings,
                    args.one_per_day,
                    args.dry_run,
                )
            elif args.mode == "sniper":
                run_sniper(
                    client,
                    ic,
                    last4,
                    parse_target_dates(args.dates) if args.dates else None,
                    parse_target_slots(args.slots),
                    args.snipe_time,
                    args.pre_seconds,
                    args.snipe_interval,
                    args.snipe_duration,
                    args.max_bookings,
                    args.one_per_day,
                    args.dry_run,
                )
    except KeyboardInterrupt:
        print("\nDihentikan oleh user.")
    except ApiError as exc:
        print(f"\nError API: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


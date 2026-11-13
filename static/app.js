(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const PAGE = document.body.dataset.page || 'login';
  const CACHE_KEY = 'locum_dash_cache_v1';
  const DAY_NAMES_SHORT = ['Min', 'Sen', 'Sel', 'Rab', 'Kam', 'Jum', 'Sab'];
  const DEFAULT_SLOT_LABELS = {
    '0900-1300': '9am – 1pm',
    '1400-1800': '2pm – 6pm',
    '1500-1900': '3pm – 7pm',
    '1800-2200': '6pm – 10pm',
    '1900-2300': '7pm – 11pm',
  };

  const state = {
    ic: null,
    profile: null,
    days: [],
    uniqueSlots: [],
    bookings: [],
    cutoff: 24,
    jobs: [],
    results: [],
    historySummary: { total: 0, success: 0, failed: 0, dry_run: 0 },
    histFilter: 'ALL',
    schedFilter: 'all',
    selectedDates: [],
    customFutureDates: [],
    selectedSlots: [],
    jobMode: 'scheduled',
    autoNewDate: '',
    serverOffsetMs: 0,
    logJobId: null,
    loadingAvail: false,
    availLoadedOnce: false,
    availError: null,
    loadingJobs: false,
    jobsLoadedOnce: false,
    modalAction: null,
  };

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function formatDatesLabel(dates) {
    return (dates && dates.length)
      ? dates.map((d) => d === 'AUTO_30D' ? 'Baru(+30h)' : d).join(', ')
      : 'Semua tanggal';
  }

  function formatSlotsLabel(slots) {
    if (!slots || !slots.length) return 'Semua jam';
    return slots.map((k) => {
      const found = (state.uniqueSlots || []).find((s) => s.key === k);
      const lbl = (found && found.label) || DEFAULT_SLOT_LABELS[k];
      return lbl ? `${lbl} (${k})` : k;
    }).join(' → ');
  }

  function setText(sel, val) {
    const el = $(sel);
    if (el) el.textContent = val;
  }

  function toast(msg) {
    const el = $('#toast');
    if (!el) return;
    el.textContent = msg;
    el.classList.add('on');
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove('on'), 3600);
  }

  async function api(url, opts = {}) {
    const res = await fetch(url, {
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
      ...opts,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || data.error || `HTTP ${res.status}`);
    }
    return data;
  }

  /* ---------- Session Cache Across Page Navigation ---------- */
  function saveCache() {
    if (!state.ic) return;
    try {
      sessionStorage.setItem(CACHE_KEY, JSON.stringify({
        ic: state.ic,
        days: state.days,
        uniqueSlots: state.uniqueSlots,
        bookings: state.bookings,
        cutoff: state.cutoff,
        availLoadedOnce: state.availLoadedOnce,
        jobs: state.jobs,
        results: state.results,
        historySummary: state.historySummary,
        jobsLoadedOnce: state.jobsLoadedOnce,
      }));
    } catch {
      // ignore quota errors
    }
  }

  function restoreCache(ic) {
    try {
      const c = JSON.parse(sessionStorage.getItem(CACHE_KEY));
      if (!c || c.ic !== ic) return false;
      if (Array.isArray(c.days)) state.days = c.days;
      if (Array.isArray(c.uniqueSlots)) state.uniqueSlots = c.uniqueSlots;
      if (Array.isArray(c.bookings)) state.bookings = c.bookings;
      if (typeof c.cutoff === 'number') state.cutoff = c.cutoff;
      if (c.availLoadedOnce) state.availLoadedOnce = true;
      if (Array.isArray(c.jobs)) state.jobs = c.jobs;
      if (Array.isArray(c.results)) state.results = c.results;
      if (c.historySummary) state.historySummary = c.historySummary;
      if (c.jobsLoadedOnce) state.jobsLoadedOnce = true;
      return true;
    } catch {
      return false;
    }
  }

  /* ---------- Theme & Clocks ---------- */
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('locum_theme', theme);
    $$('.theme-toggle').forEach((b) => {
      b.innerHTML = theme === 'dark'
        ? '<i class="fa-solid fa-sun"></i>'
        : '<i class="fa-solid fa-moon"></i>';
    });
  }

  $$('.theme-toggle').forEach((btn) => {
    btn.onclick = () => {
      const cur = document.documentElement.getAttribute('data-theme') || 'light';
      applyTheme(cur === 'dark' ? 'light' : 'dark');
    };
  });
  applyTheme(localStorage.getItem('locum_theme') || 'light');

  function fmtTime(dateObj, tzOffsetHours) {
    const utcMs = dateObj.getTime() + dateObj.getTimezoneOffset() * 60000;
    const d = new Date(utcMs + tzOffsetHours * 3600000);
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  function updateJobCountdowns() {
    const nowMs = Date.now() + state.serverOffsetMs;
    $$('.countdown-box[data-next-iso]').forEach((el) => {
      const rawIso = el.dataset.nextIso;
      const mytTxt = el.dataset.nextMyt || '';
      if (!rawIso) return;
      const targetMs = new Date(rawIso).getTime();
      const diff = Math.max(0, Math.floor((targetMs - nowMs) / 1000));
      const days = Math.floor(diff / 86400);
      const h = Math.floor((diff % 86400) / 3600);
      const m = Math.floor((diff % 3600) / 60);
      const s = diff % 60;
      const pad = (n) => String(n).padStart(2, '0');
      const durTxt = days > 0 ? `${days} hari ${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(h)}:${pad(m)}:${pad(s)}`;
      el.innerHTML = diff > 0
        ? `<i class="fa-solid fa-hourglass-half"></i> Mulai dalam ${durTxt} (${esc(mytTxt)})`
        : '<i class="fa-solid fa-bolt"></i> Sedang mengeksekusi…';
    });
  }

  function tickClocks() {
    const now = new Date(Date.now() + state.serverOffsetMs);
    const myt = fmtTime(now, 8);
    const wib = fmtTime(now, 7);
    setText('#clockMyt', myt);
    setText('#clockWib', wib);
    setText('#clockMytLogin', myt);
    setText('#clockWibLogin', wib);
    setText('#clockMytMob', myt.slice(0, 5));
    setText('#clockWibMob', wib.slice(0, 5));
    updateJobCountdowns();
  }

  async function checkHealth() {
    const warns = [$('#dbWarning'), $('#dbWarningLogin')].filter(Boolean);
    try {
      const h = await api('/api/health');
      const serverUtc = h.server_epoch_ms || new Date(h.server_time_wib.replace(' ', 'T') + '+07:00').getTime();
      if (!Number.isNaN(serverUtc)) {
        state.serverOffsetMs = serverUtc - Date.now();
      }
      state.autoNewDate = h.auto_new_date || '';
      setText('#autoNewDateTxt', state.autoNewDate || '-');

      if (h.db_ok) {
        warns.forEach((w) => w.classList.add('hidden'));
      } else {
        const msgHtml = '<i class="fa-solid fa-triangle-exclamation"></i> <b>MySQL belum berjalan:</b> Jalankan service <b>MySQL</b> di <b>XAMPP Control Panel</b> (Port 3306) atau sesuaikan port/password di <code>.env</code> agar jadwal &amp; log tersimpan di phpMyAdmin.';
        warns.forEach((w) => { w.innerHTML = msgHtml; w.classList.remove('hidden'); });
      }

      const dot = $('#activeJobDot');
      if (dot) {
        if (h.active_jobs && h.active_jobs.length > 0) dot.classList.remove('hidden');
        else dot.classList.add('hidden');
      }
    } catch {
      // ignore transient poll errors
    }
  }

  /* ---------- Modal ---------- */
  function closeModal() {
    const back = $('#modalBack');
    if (back) back.classList.add('hidden');
    state.modalAction = null;
  }

  function openModal(title, bodyHtml, onConfirm, confirmText = 'Ya, Lanjutkan', danger = false, infoOnly = false) {
    const back = $('#modalBack');
    if (!back) return;
    setText('#modalTitle', title);
    const body = $('#modalBody');
    if (body) body.innerHTML = bodyHtml;
    const btn = $('#modalConfirm');
    const cancelBtn = $('#modalCancel');
    if (btn) {
      btn.classList.toggle('hidden', infoOnly);
      btn.textContent = confirmText;
      btn.className = `btn ${danger ? 'danger' : 'primary'}${infoOnly ? ' hidden' : ''}`;
    }
    if (cancelBtn) cancelBtn.textContent = infoOnly ? 'Tutup' : 'Batal';
    state.modalAction = onConfirm;
    back.classList.remove('hidden');
  }

  function bindGlobalModalEvents() {
    const cancel = $('#modalCancel');
    const back = $('#modalBack');
    const confirm = $('#modalConfirm');
    if (cancel) cancel.onclick = closeModal;
    if (back) back.onclick = (e) => { if (e.target.id === 'modalBack') closeModal(); };
    if (confirm) {
      confirm.onclick = async () => {
        const fn = state.modalAction;
        closeModal();
        if (fn) await fn();
      };
    }
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        const jobModal = $('#jobModalBack');
        if (jobModal) jobModal.classList.add('hidden');
        closeModal();
      }
    });
  }

  /* ---------- Loaders & KPIs ---------- */
  function renderInlineLoader(msg) {
    return `<div class="loader-inline"><i class="fa-solid fa-circle-notch fa-spin"></i><span>${esc(msg)}</span></div>`;
  }

  function renderScheduleLoader() {
    const skels = Array.from({ length: 6 }, () => `
      <div class="skel-card">
        <div class="skel-line"></div>
        <div class="skel-line sm"></div>
        <div class="skel-line box"></div>
        <div class="skel-line box"></div>
      </div>
    `).join('');
    return `
      <div class="loader-banner">
        <i class="fa-solid fa-circle-notch fa-spin"></i>
        <div>
          <div><b>Mengambil data jadwal &amp; ketersediaan slot…</b></div>
          <small class="muted">Menghubungi server Locum ED HJ, mohon tunggu sebentar.</small>
        </div>
      </div>
      ${skels}
    `;
  }

  function updateKpis() {
    const openSlots = state.days.reduce((acc, d) => acc + (d.open_count || 0), 0);
    const activeJobs = state.jobs.filter((j) => j.status === 'waiting' || j.status === 'running').length;
    setText('#kpiOpen', String(openSlots));
    setText('#kpiBooked', String(state.bookings.length));
    setText('#kpiJobs', String(activeJobs));
    setText('#kpiSuccess', String((state.historySummary && state.historySummary.success) || 0));
  }

  async function loadAvailability({ onUpdate } = {}) {
    if (!state.ic) return;
    state.loadingAvail = true;
    state.availError = null;
    const refreshBtn = $('#btnRefreshAll');
    if (refreshBtn) {
      refreshBtn.disabled = true;
      refreshBtn.innerHTML = '<i class="fa-solid fa-rotate-right fa-spin"></i> Memuat…';
    }
    if (!state.availLoadedOnce && onUpdate) onUpdate();
    try {
      const data = await api(`/api/availability?ic=${encodeURIComponent(state.ic)}`);
      state.days = data.days || [];
      state.uniqueSlots = data.unique_slots || [];
      state.bookings = data.bookings || [];
      state.cutoff = data.cutoff || 24;
      state.availLoadedOnce = true;
      updateKpis();
      saveCache();
    } catch (err) {
      state.availError = err.message || 'Gagal mengambil jadwal dari server.';
      throw err;
    } finally {
      state.loadingAvail = false;
      if (refreshBtn) {
        refreshBtn.disabled = false;
        refreshBtn.innerHTML = '<i class="fa-solid fa-rotate-right"></i> Refresh';
      }
      if (onUpdate) onUpdate();
    }
  }

  async function loadJobs({ onUpdate } = {}) {
    if (!state.ic) return;
    if (!state.jobsLoadedOnce) {
      state.loadingJobs = true;
      if (onUpdate) onUpdate();
    }
    try {
      const res = await api(`/api/jobs?ic=${encodeURIComponent(state.ic)}`);
      state.jobs = res.jobs || [];
      state.results = res.results || [];
      state.historySummary = res.summary || { total: 0, success: 0, failed: 0, dry_run: 0 };
      state.jobsLoadedOnce = true;
      updateKpis();
      saveCache();
    } catch {
      // ignore if MySQL offline
    } finally {
      state.loadingJobs = false;
      if (onUpdate) onUpdate();
    }
  }

  /* ---------- Auth & Profile Shell ---------- */
  async function logout() {
    localStorage.removeItem('locum_dash_user');
    sessionStorage.removeItem(CACHE_KEY);
    try {
      await fetch('/api/logout', { method: 'POST' });
    } catch {
      // ignore
    }
    window.location.replace('/login');
  }

  function initAppShell() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem('locum_dash_user')); } catch { saved = null; }
    if (!saved || !saved.profile || !saved.profile.ic) {
      window.location.replace('/login');
      return false;
    }
    const profile = saved.profile;
    state.ic = profile.ic;
    state.profile = profile;

    setText('#profAvatar', (profile.name || 'U').charAt(0).toUpperCase());
    setText('#profName', profile.name || profile.ic);
    setText('#mobProfName', profile.name || profile.ic);
    setText('#profRole', profile.role || 'MO');
    setText('#profReg', profile.regNo || '-');
    setText('#profWork', profile.workplace || '-');

    const btnLogout = $('#btnLogout');
    const btnLogoutMob = $('#btnLogoutMob');
    if (btnLogout) btnLogout.onclick = logout;
    if (btnLogoutMob) btnLogoutMob.onclick = logout;

    bindGlobalModalEvents();
    restoreCache(profile.ic);
    updateKpis();
    return true;
  }

  /* ---------- PAGE: LOGIN ---------- */
  function initLoginPage() {
    async function loadSavedAccounts() {
      try {
        const res = await api('/api/accounts');
        const box = $('#savedAccountsBox');
        const list = $('#savedAccountsList');
        if (!box || !list) return;
        if (!res.accounts || !res.accounts.length) {
          box.classList.add('hidden');
          return;
        }
        box.classList.remove('hidden');
        list.innerHTML = res.accounts.map((a) =>
          `<button type="button" class="chip-btn" data-ic="${esc(a.ic_number)}">${esc(a.name || a.ic_number)} (${esc(a.role)})</button>`
        ).join('');
        $$('.chip-btn', list).forEach((btn) => {
          btn.onclick = () => {
            $('#loginIc').value = btn.dataset.ic;
            $('#loginLast4').focus();
          };
        });
      } catch {
        // ignore if MySQL offline
      }
    }

    const togglePin = $('#togglePin');
    if (togglePin) {
      togglePin.onclick = () => {
        const inp = $('#loginLast4');
        const show = inp.type === 'password';
        inp.type = show ? 'text' : 'password';
        togglePin.innerHTML = show
          ? '<i class="fa-regular fa-eye-slash"></i>'
          : '<i class="fa-regular fa-eye"></i>';
      };
    }

    const icInp = $('#loginIc');
    const last4Inp = $('#loginLast4');
    if (icInp) icInp.addEventListener('input', (e) => { e.target.value = e.target.value.replace(/\D/g, '').slice(0, 12); });
    if (last4Inp) last4Inp.addEventListener('input', (e) => { e.target.value = e.target.value.replace(/\D/g, '').slice(0, 4); });

    const form = $('#loginForm');
    if (form) {
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const errEl = $('#loginErr');
        const btn = $('#loginBtn');
        errEl.classList.add('hidden');
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Memeriksa…';
        try {
          const res = await api('/api/login', {
            method: 'POST',
            body: JSON.stringify({
              ic: $('#loginIc').value,
              last4: $('#loginLast4').value,
            }),
          });
          localStorage.setItem('locum_dash_user', JSON.stringify({ ic: res.profile.ic, profile: res.profile }));
          sessionStorage.removeItem(CACHE_KEY);
          window.location.href = '/jadwal';
        } catch (err) {
          errEl.textContent = err.message;
          errEl.classList.remove('hidden');
        } finally {
          btn.disabled = false;
          btn.innerHTML = 'Masuk ke Dashboard <i class="fa-solid fa-arrow-right"></i>';
        }
      });
    }

    loadSavedAccounts();
  }

  /* ---------- PAGE: JADWAL ---------- */
  function initJadwalPage() {
    function renderSchedule() {
      const grid = $('#scheduleGrid');
      if (!grid) return;
      if (state.loadingAvail && !state.availLoadedOnce) {
        grid.innerHTML = renderScheduleLoader();
        return;
      }
      if (state.availError && !state.availLoadedOnce) {
        grid.innerHTML = `
          <div class="loader-banner">
            <i class="fa-solid fa-circle-exclamation" style="color:var(--bad)"></i>
            <div style="flex:1">
              <div><b>Gagal memuat jadwal dari server</b></div>
              <small class="muted">${esc(state.availError)}</small>
            </div>
            <button type="button" id="btnRetryAvail" class="btn primary sm"><i class="fa-solid fa-rotate-right"></i> Coba Lagi</button>
          </div>
        `;
        const retry = $('#btnRetryAvail');
        if (retry) retry.onclick = () => loadAvailability({ onUpdate: renderSchedule }).catch((e) => toast(e.message));
        return;
      }

      const f = state.schedFilter;
      const filtered = state.days.filter((d) => {
        if (f === 'open') return d.open_count > 0;
        if (f === 'booked') return d.booked_count > 0;
        return true;
      });

      if (!filtered.length) {
        grid.innerHTML = `<div class="panel-card muted" style="grid-column:1/-1">Tidak ada jadwal yang cocok dengan filter "${esc(f)}".</div>`;
        return;
      }

      grid.innerHTML = filtered.map((d) => {
        const slotsHtml = d.slots.map((s) => {
          let tag = 'FULL';
          if (s.status === 'open') tag = `OPEN ${s.left}/${s.total}`;
          else if (s.status === 'booked') tag = '<i class="fa-solid fa-check"></i> BOOKED';
          else if (s.status === 'closed') tag = 'CLOSED';
          return `<button type="button" class="slot-btn ${s.status}" data-date="${esc(d.date)}" data-slot="${esc(s.key)}" data-label="${esc(s.label)}" ${s.status === 'open' ? '' : 'disabled'}>
            <span>${esc(s.label)} <small class="muted">(${esc(s.key)})</small></span>
            <span class="slot-tag">${tag}</span>
          </button>`;
        }).join('');

        return `<div class="day-card ${d.open_count > 0 ? 'has-open' : ''}">
          <div class="day-head">
            <span class="day-title">${esc(d.date)} (${esc(d.day_name)})</span>
            ${d.holiday ? '<span class="ph-tag">Public Holiday</span>' : ''}
          </div>
          <div class="slot-list">${slotsHtml}</div>
          <button type="button" class="btn soft sm full pick-for-bot" data-date="${esc(d.date)}"><i class="fa-solid fa-robot"></i> Atur Bot Tanggal Ini</button>
        </div>`;
      }).join('');

      $$('.slot-btn.open', grid).forEach((btn) => {
        btn.onclick = () => {
          const { date, slot, label } = btn.dataset;
          openModal(
            'Booking Slot Sekarang?',
            `Anda akan booking jadwal <b>${esc(date)}</b> jam <b>${esc(label)}</b>.`,
            async () => {
              try {
                const res = await api('/api/book-direct', {
                  method: 'POST',
                  body: JSON.stringify({ ic: state.ic, date, slot }),
                });
                toast(`Berhasil booking! Ref: ${res.booking.id || '-'}`);
                await Promise.all([loadAvailability({ onUpdate: renderSchedule }), loadJobs()]);
              } catch (err) {
                toast(`Gagal booking: ${err.message}`);
              }
            },
            'Ya, Booking Sekarang'
          );
        };
      });

      $$('.pick-for-bot', grid).forEach((btn) => {
        btn.onclick = () => {
          window.location.href = `/auto-book?date=${encodeURIComponent(btn.dataset.date)}`;
        };
      });
    }

    $$('[data-sched-filter]').forEach((b) => {
      b.onclick = () => {
        state.schedFilter = b.dataset.schedFilter;
        $$('[data-sched-filter]').forEach((x) => x.classList.toggle('active', x === b));
        renderSchedule();
      };
    });

    const refreshBtn = $('#btnRefreshAll');
    if (refreshBtn) {
      refreshBtn.onclick = async () => {
        try {
          await Promise.all([checkHealth(), loadAvailability({ onUpdate: renderSchedule }), loadJobs()]);
          toast('Jadwal diperbarui.');
        } catch (err) {
          toast(err.message);
        }
      };
    }

    renderSchedule();
    loadAvailability({ onUpdate: renderSchedule }).catch((err) => toast(err.message));
    loadJobs();
  }

  /* ---------- PAGE: AUTO-BOOK ---------- */
  function initAutoBookPage() {
    function addDaysIso(isoStr, days) {
      const [y, m, d] = isoStr.split('-').map(Number);
      const dt = new Date(Date.UTC(y, m - 1, d + days));
      return dt.toISOString().slice(0, 10);
    }

    function weekdayId(isoStr) {
      const [y, m, d] = isoStr.split('-').map(Number);
      return DAY_NAMES_SHORT[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
    }

    function releaseDateOf(targetIso) {
      return addDaysIso(targetIso, -30);
    }

    function getLastAvailDate() {
      if (state.days.length) return state.days[state.days.length - 1].date;
      if (state.autoNewDate) return addDaysIso(state.autoNewDate, -1);
      return null;
    }

    function isUnreleasedDate(d) {
      if (!d || d === 'AUTO_30D') return false;
      const availSet = new Set(state.days.map((x) => x.date));
      if (availSet.has(d)) return false;
      const last = getLastAvailDate();
      return !last || d > last;
    }

    function updateSchedPreview() {
      const prev = $('#schedPreview');
      if (!prev) return;
      const t = ($('#schedTime') && $('#schedTime').value) || '00:00';
      const tz = ($('#schedTz') && $('#schedTz').value) || 'MYT';
      const [hh, mm] = t.split(':').map(Number);
      const otherTz = tz === 'MYT' ? 'WIB' : 'MYT';
      const shiftedH = (hh + (tz === 'MYT' ? -1 : 1) + 24) % 24;
      const otherStr = `${String(shiftedH).padStart(2, '0')}:${String(mm || 0).padStart(2, '0')}`;
      prev.textContent = `Bot standby & mulai eksekusi rapid-booking tepat pukul ${t} ${tz} (= ${otherStr} ${otherTz}).`;
    }

    function syncFutureAutoNote() {
      const box = $('#futureAutoBox');
      const note = $('#futureAutoNote');
      const autoCheck = $('#autoReleaseMidnight');
      if (!box || !note || !autoCheck) return;
      const unreleased = state.selectedDates.filter(isUnreleasedDate);
      if (!unreleased.length) {
        box.classList.add('hidden');
        return;
      }
      box.classList.remove('hidden');
      const t = ($('#schedTime') && $('#schedTime').value) || '00:00';
      const tz = ($('#schedTz') && $('#schedTz').value) || 'MYT';
      if (autoCheck.checked) {
        if (unreleased.length === 1) {
          const rel = releaseDateOf(unreleased[0]);
          if ($('#schedDate')) $('#schedDate').value = rel;
          note.innerHTML = `Tanggal <b>${esc(unreleased[0])}</b> belum rilis di jadwal web. Bot diset mulai tepat saat hari bukanya: <b>${esc(rel)}</b> jam <b>${esc(t)} ${esc(tz)}</b>.`;
        } else {
          if ($('#schedDate')) $('#schedDate').value = '';
          note.innerHTML = `<b>${unreleased.length} tanggal mendatang</b> dipilih (${unreleased.map((x) => esc(x.slice(5))).join(', ')}). Saat disimpan akan dibuat <b>${unreleased.length} scheduler terpisah</b>, masing-masing mulai di hari bukanya (H-30) jam <b>${esc(t)} ${esc(tz)}</b>.`;
        }
      } else {
        note.textContent = 'Mode manual: scheduler memakai tanggal & jam jalan yang Anda isi di atas.';
      }
      updateSchedPreview();
    }

    function renderPickers() {
      const autoBtn = $('#btnAuto30d');
      if (autoBtn) autoBtn.classList.toggle('selected', state.selectedDates.includes('AUTO_30D'));

      const dp = $('#pickerDates');
      if (dp) {
        if (!state.days.length && state.loadingAvail) {
          dp.innerHTML = '<p class="muted small" style="grid-column:1/-1;margin:4px">Memuat daftar tanggal dari server Locum…</p>';
        } else {
          dp.innerHTML = state.days.map((d) => {
            const sel = state.selectedDates.includes(d.date);
            const badge = d.open_count > 0 ? 'OPEN' : d.booked_count > 0 ? 'BOOKED' : 'FULL';
            return `<button type="button" class="date-chip ${sel ? 'selected' : ''}" data-date="${esc(d.date)}">
              <span>${esc(d.date.slice(5))}</span>
              <small>${esc(d.day_name)} · ${badge}</small>
            </button>`;
          }).join('');
          $$('.date-chip', dp).forEach((btn) => {
            btn.onclick = () => {
              const val = btn.dataset.date;
              const idx = state.selectedDates.indexOf(val);
              if (idx >= 0) state.selectedDates.splice(idx, 1);
              else state.selectedDates.push(val);
              renderPickers();
            };
          });
        }
      }

      const fp = $('#pickerFutureDates');
      if (fp) {
        const lastAvail = getLastAvailDate();
        const futureSet = new Set();
        if (lastAvail) {
          for (let i = 1; i <= 14; i += 1) futureSet.add(addDaysIso(lastAvail, i));
          const inp = $('#customFutureDate');
          if (inp) inp.min = addDaysIso(lastAvail, 1);
        }
        state.customFutureDates.forEach((d) => { if (isUnreleasedDate(d)) futureSet.add(d); });
        state.selectedDates.forEach((d) => { if (isUnreleasedDate(d)) futureSet.add(d); });
        const futureDates = Array.from(futureSet).sort();

        fp.innerHTML = futureDates.length
          ? futureDates.map((d) => {
              const sel = state.selectedDates.includes(d);
              const rel = releaseDateOf(d);
              return `<button type="button" class="date-chip future ${sel ? 'selected' : ''}" data-date="${esc(d)}">
                <span>${esc(d.slice(5))}</span>
                <small>${esc(weekdayId(d))} · Buka ${esc(rel.slice(8))}/${esc(rel.slice(5, 7))}</small>
              </button>`;
            }).join('')
          : '<p class="muted small" style="grid-column:1/-1;margin:4px">Tambahkan tanggal mendatang pada kolom di bawah.</p>';

        $$('.date-chip', fp).forEach((btn) => {
          btn.onclick = () => {
            const val = btn.dataset.date;
            const idx = state.selectedDates.indexOf(val);
            if (idx >= 0) state.selectedDates.splice(idx, 1);
            else state.selectedDates.push(val);
            renderPickers();
          };
        });
      }

      const dHint = $('#selectedDatesHint');
      if (dHint) {
        dHint.textContent = !state.selectedDates.length
          ? 'Belum ada tanggal dipilih = pantau SEMUA tanggal.'
          : `Target tanggal (${state.selectedDates.length}): ${state.selectedDates.map((x) => x === 'AUTO_30D' ? `Baru (${state.autoNewDate || 'AUTO'})` : x).join(', ')}`;
      }

      syncFutureAutoNote();

      const sp = $('#pickerSlots');
      if (sp) {
        const slots = state.uniqueSlots.length ? state.uniqueSlots : [
          { key: '0900-1300', label: '9am – 1pm' },
          { key: '1400-1800', label: '2pm – 6pm' },
          { key: '1500-1900', label: '3pm – 7pm' },
          { key: '1800-2200', label: '6pm – 10pm' },
          { key: '1900-2300', label: '7pm – 11pm' },
        ];
        sp.innerHTML = slots.map((s) => {
          const idx = state.selectedSlots.indexOf(s.key);
          const sel = idx >= 0;
          return `<button type="button" class="shift-chip ${sel ? 'selected' : ''}" data-key="${esc(s.key)}">
            ${sel ? `<span class="prio-num">#${idx + 1}</span>` : ''}
            <span>${esc(s.label)}</span>
            <small class="muted">(${esc(s.key)})</small>
          </button>`;
        }).join('');

        $$('.shift-chip', sp).forEach((btn) => {
          btn.onclick = () => {
            const key = btn.dataset.key;
            const idx = state.selectedSlots.indexOf(key);
            if (idx >= 0) state.selectedSlots.splice(idx, 1);
            else state.selectedSlots.push(key);
            renderPickers();
          };
        });
      }

      const sHint = $('#selectedSlotsHint');
      if (sHint) {
        sHint.textContent = state.selectedSlots.length
          ? `Urutan prioritas jam: ${state.selectedSlots.join(' → ')}`
          : 'Tidak dipilih = ambil jam mana saja yang kosong.';
      }
    }

    function openJobModal() {
      renderPickers();
      updateSchedPreview();
      const back = $('#jobModalBack');
      if (back) back.classList.remove('hidden');
    }

    function closeJobModal() {
      const back = $('#jobModalBack');
      if (back) back.classList.add('hidden');
    }

    function renderJobs() {
      const el = $('#jobsList');
      if (!el) return;
      if (state.loadingJobs && !state.jobsLoadedOnce) {
        el.innerHTML = renderInlineLoader('Memuat antrian & jadwal bot…');
        return;
      }
      if (!state.jobs.length) {
        el.innerHTML = '<p class="muted small">Belum ada jadwal bot tersimpan di database MySQL.</p>';
        return;
      }
      el.innerHTML = state.jobs.map((j) => {
        const datesTxt = formatDatesLabel(j.target_dates);
        const slotsTxt = formatSlotsLabel(j.target_slots);
        const timeInfo = j.mode === 'scheduled'
          ? `<i class="fa-regular fa-clock"></i> Jam ${esc((j.schedule_time || '').slice(0, 5))} ${esc(j.schedule_tz)}${j.schedule_date ? ` (${esc(j.schedule_date)})` : ''}`
          : `<i class="fa-solid fa-arrows-rotate"></i> Pantau tiap ${esc(j.poll_interval)}d`;
        const isLive = j.status === 'waiting' || j.status === 'running';

        return `<div class="job-card">
          <div class="job-head">
            <div>
              <div class="job-title">${esc(j.title)} ${j.dry_run ? '<span class="badge">DRY-RUN</span>' : ''}</div>
              <div class="muted small">${timeInfo} · Berhasil: ${esc(j.success_count)} shift</div>
            </div>
            <span class="st-pill st-${esc(j.status)}">${esc(j.status)}</span>
          </div>
          <div class="small"><b>Tanggal:</b> ${esc(datesTxt)}<br><b>Jam:</b> ${esc(slotsTxt)}</div>
          ${(j.status === 'waiting' && j.next_run_iso) ? `<div class="countdown-box" data-next-iso="${esc(j.next_run_iso)}" data-next-myt="${esc(j.next_run_myt || '')}">Menghitung…</div>` : ''}
          ${j.last_message ? `<div class="muted small">${esc(j.last_message)}</div>` : ''}
          <div class="btn-row">
            ${isLive
              ? `<button type="button" class="btn danger sm job-stop" data-id="${j.id}"><i class="fa-solid fa-stop"></i> Stop</button>`
              : `<button type="button" class="btn primary sm job-start" data-id="${j.id}"><i class="fa-solid fa-play"></i> Jalankan</button>`}
            <button type="button" class="btn soft sm job-logs" data-id="${j.id}"><i class="fa-solid fa-terminal"></i> Log</button>
            <button type="button" class="btn soft sm job-del" data-id="${j.id}"><i class="fa-solid fa-trash"></i> Hapus</button>
          </div>
        </div>`;
      }).join('');

      updateJobCountdowns();

      $$('.job-start', el).forEach((b) => {
        b.onclick = async () => {
          try {
            await api(`/api/jobs/${b.dataset.id}/start?ic=${encodeURIComponent(state.ic)}`, { method: 'POST' });
            toast('Bot dimulai!');
            await loadJobs({ onUpdate: renderJobs });
          } catch (err) { toast(err.message); }
        };
      });
      $$('.job-stop', el).forEach((b) => {
        b.onclick = async () => {
          try {
            await api(`/api/jobs/${b.dataset.id}/stop?ic=${encodeURIComponent(state.ic)}`, { method: 'POST' });
            toast('Bot dihentikan.');
            await loadJobs({ onUpdate: renderJobs });
          } catch (err) { toast(err.message); }
        };
      });
      $$('.job-logs', el).forEach((b) => {
        b.onclick = () => {
          window.location.href = `/logs?job_id=${encodeURIComponent(b.dataset.id)}`;
        };
      });
      $$('.job-del', el).forEach((b) => {
        b.onclick = () => {
          openModal('Hapus Jadwal?', 'Jadwal ini akan dihapus dari database MySQL.', async () => {
            try {
              await api(`/api/jobs/${b.dataset.id}?ic=${encodeURIComponent(state.ic)}`, { method: 'DELETE' });
              toast('Jadwal dihapus.');
              await loadJobs({ onUpdate: renderJobs });
            } catch (err) { toast(err.message); }
          }, 'Ya, Hapus', true);
        };
      });
    }

    async function submitJob(startNow) {
      if (!state.ic) return;
      try {
        const baseTitle = $('#jobTitle').value.trim();
        const schedTime = $('#schedTime').value || '00:00';
        const schedTz = $('#schedTz').value;
        const pollInterval = parseFloat($('#pollInterval').value || '2');
        const onePerDay = $('#onePerDay').checked;
        const dryRun = $('#dryRun').checked;
        const autoRel = $('#autoReleaseMidnight').checked && state.jobMode === 'scheduled';

        const unreleased = state.selectedDates.filter(isUnreleasedDate);
        const normalDates = state.selectedDates.filter((d) => !isUnreleasedDate(d));

        let createdCount = 0;

        if (autoRel && unreleased.length > 1) {
          if (normalDates.length) {
            await api('/api/jobs', {
              method: 'POST',
              body: JSON.stringify({
                ic: state.ic,
                title: baseTitle,
                mode: state.jobMode,
                target_dates: normalDates,
                target_slots: [...state.selectedSlots],
                schedule_time: schedTime,
                schedule_tz: schedTz,
                schedule_date: $('#schedDate').value || null,
                poll_interval: pollInterval,
                max_bookings: 1,
                one_per_day: onePerDay,
                dry_run: dryRun,
                start_now: startNow,
              }),
            });
            createdCount += 1;
          }
          for (const d of unreleased) {
            const relDate = releaseDateOf(d);
            const jobTitle = baseTitle ? `${baseTitle} (${d})` : `Sniper ${d} (${schedTime} ${schedTz})`;
            await api('/api/jobs', {
              method: 'POST',
              body: JSON.stringify({
                ic: state.ic,
                title: jobTitle,
                mode: 'scheduled',
                target_dates: [d],
                target_slots: [...state.selectedSlots],
                schedule_time: schedTime,
                schedule_tz: schedTz,
                schedule_date: relDate,
                poll_interval: pollInterval,
                max_bookings: 1,
                one_per_day: onePerDay,
                dry_run: dryRun,
                start_now: startNow,
              }),
            });
            createdCount += 1;
          }
        } else {
          const singleSchedDate = (autoRel && unreleased.length === 1)
            ? ($('#schedDate').value || releaseDateOf(unreleased[0]))
            : ($('#schedDate').value || null);
          await api('/api/jobs', {
            method: 'POST',
            body: JSON.stringify({
              ic: state.ic,
              title: baseTitle,
              mode: state.jobMode,
              target_dates: [...state.selectedDates],
              target_slots: [...state.selectedSlots],
              schedule_time: schedTime,
              schedule_tz: schedTz,
              schedule_date: singleSchedDate,
              poll_interval: pollInterval,
              max_bookings: 1,
              one_per_day: onePerDay,
              dry_run: dryRun,
              start_now: startNow,
            }),
          });
          createdCount = 1;
        }

        closeJobModal();
        toast(
          createdCount > 1
            ? `${createdCount} scheduler tanggal mendatang berhasil disimpan${startNow ? ' & dijalankan!' : '.'}`
            : (startNow ? 'Jadwal disimpan & bot mulai berjalan!' : 'Jadwal disimpan ke MySQL.')
        );
        $('#jobTitle').value = '';
        await loadJobs({ onUpdate: renderJobs });
      } catch (err) {
        toast(`Gagal menyimpan jadwal: ${err.message}`);
      }
    }

    $('#btnOpenJobModal').onclick = openJobModal;
    $('#btnCloseJobModal').onclick = closeJobModal;
    $('#jobModalBack').onclick = (e) => { if (e.target.id === 'jobModalBack') closeJobModal(); };
    $('#btnRefreshJobs').onclick = () => loadJobs({ onUpdate: renderJobs });

    $('#btnAddFutureDate').onclick = () => {
      const val = $('#customFutureDate').value;
      if (!val) {
        toast('Pilih tanggal terlebih dahulu.');
        return;
      }
      if (!state.customFutureDates.includes(val)) state.customFutureDates.push(val);
      if (!state.selectedDates.includes(val)) state.selectedDates.push(val);
      $('#customFutureDate').value = '';
      renderPickers();
      toast(`Tanggal ${val} ditambahkan ke target.`);
    };
    $('#autoReleaseMidnight').onchange = syncFutureAutoNote;

    $('#btnAuto30d').onclick = () => {
      const idx = state.selectedDates.indexOf('AUTO_30D');
      if (idx >= 0) state.selectedDates.splice(idx, 1);
      else state.selectedDates.push('AUTO_30D');
      renderPickers();
    };
    $('#btnPickAllDates').onclick = () => {
      state.selectedDates = state.days.map((d) => d.date);
      renderPickers();
    };
    $('#btnPickWeekend').onclick = () => {
      state.selectedDates = state.days.filter((d) => d.day_name === 'Sab' || d.day_name === 'Min').map((d) => d.date);
      renderPickers();
    };
    $('#btnClearDates').onclick = () => { state.selectedDates = []; renderPickers(); };
    $('#btnClearSlots').onclick = () => { state.selectedSlots = []; renderPickers(); };

    $('#schedTime').oninput = () => { updateSchedPreview(); syncFutureAutoNote(); };
    $('#schedTz').onchange = () => { updateSchedPreview(); syncFutureAutoNote(); };
    $('#btnPresetMidnight').onclick = () => {
      $('#schedTime').value = '00:00';
      $('#schedTz').value = 'MYT';
      $('#schedDate').value = '';
      if (!state.selectedDates.length) state.selectedDates = ['AUTO_30D'];
      renderPickers();
      updateSchedPreview();
      toast('Preset 12 Tengah Malam MYT (00:00 MYT / 23:00 WIB) diterapkan.');
    };

    $$('.mode-btn').forEach((btn) => {
      btn.onclick = () => {
        state.jobMode = btn.dataset.mode;
        $$('.mode-btn').forEach((b) => b.classList.toggle('active', b === btn));
        $('#scheduledBox').classList.toggle('hidden', state.jobMode !== 'scheduled');
        $('#monitorBox').classList.toggle('hidden', state.jobMode !== 'monitor');
      };
    });

    $('#jobForm').addEventListener('submit', (e) => { e.preventDefault(); submitJob(true); });
    $('#btnSaveOnly').onclick = () => submitJob(false);

    const paramDate = new URLSearchParams(window.location.search).get('date');
    if (paramDate) {
      if (!state.selectedDates.includes(paramDate)) state.selectedDates.push(paramDate);
      openJobModal();
      toast(`Tanggal ${paramDate} ditambahkan ke target Auto-Book.`);
    }

    renderJobs();
    loadJobs({ onUpdate: renderJobs });
    loadAvailability({ onUpdate: renderPickers }).catch(() => {});
    return () => loadJobs({ onUpdate: renderJobs });
  }

  /* ---------- PAGE: RIWAYAT ---------- */
  function initRiwayatPage() {
    function renderMyBookings() {
      setText('#cutoffNote', `Batas cancel gratis: ${state.cutoff} jam sebelum waktu shift.`);
      const list = $('#myBookingsList');
      if (!list) return;
      if (state.loadingAvail && !state.availLoadedOnce) {
        list.innerHTML = renderInlineLoader('Memuat booking aktif…');
        return;
      }
      if (state.availError && !state.availLoadedOnce) {
        list.innerHTML = `<p class="muted small">${esc(state.availError)}</p>`;
        return;
      }
      if (!state.bookings.length) {
        list.innerHTML = '<p class="muted small">Belum ada booking aktif.</p>';
        return;
      }
      list.innerHTML = state.bookings.map((b) => `
        <div class="item-card">
          <div>
            <div><b>${esc(b.date)}</b> · ${esc(b.label || b.slot)}</div>
            <div class="muted small">Seat: <b>${esc(b.seat || '-')}</b> · Ref: <code>${esc(b.id)}</code></div>
          </div>
          ${b.canCancel
            ? `<button type="button" class="btn danger sm cancel-book" data-id="${esc(b.id)}" data-desc="${esc(b.date)} (${esc(b.label)})">Cancel</button>`
            : '<span class="muted small">Terkunci (&lt;24j)</span>'}
        </div>
      `).join('');

      $$('.cancel-book', list).forEach((btn) => {
        btn.onclick = () => {
          openModal(
            'Cancel Booking Ini?',
            `Yakin ingin membatalkan <b>${esc(btn.dataset.desc)}</b> (Ref <code>${esc(btn.dataset.id)}</code>)?`,
            async () => {
              try {
                await api('/api/cancel-booking', {
                  method: 'POST',
                  body: JSON.stringify({ ic: state.ic, booking_id: btn.dataset.id }),
                });
                toast('Booking berhasil dicancel.');
                await Promise.all([loadAvailability({ onUpdate: renderMyBookings }), loadJobs({ onUpdate: renderResults })]);
              } catch (err) { toast(err.message); }
            },
            'Ya, Cancel Booking',
            true
          );
        };
      });
    }

    function outcomeLabel(outcome) {
      if (outcome === 'SUCCESS') return '<i class="fa-solid fa-circle-check"></i> DAPAT';
      if (outcome === 'FAILED') return '<i class="fa-solid fa-circle-xmark"></i> GAGAL';
      if (outcome === 'NO_SLOT') return '<i class="fa-solid fa-triangle-exclamation"></i> TIDAK DAPAT';
      if (outcome === 'DRY_RUN') return '<i class="fa-solid fa-flask"></i> SIMULASI';
      return esc(outcome || 'SUCCESS');
    }

    function renderResults() {
      const sum = state.historySummary || { total: 0, success: 0, failed: 0, dry_run: 0 };
      const sumEl = $('#historySummary');
      if (sumEl) {
        sumEl.innerHTML = `
          <div class="stat-box"><b>${esc(sum.total)}</b><small>Total</small></div>
          <div class="stat-box ok"><b>${esc(sum.success)}</b><small>Dapat</small></div>
          <div class="stat-box bad"><b>${esc(sum.failed)}</b><small>Tidak Dapat</small></div>
          <div class="stat-box brand"><b>${esc(sum.dry_run)}</b><small>Simulasi</small></div>
        `;
      }

      const el = $('#resultsList');
      if (!el) return;
      if (state.loadingJobs && !state.jobsLoadedOnce) {
        el.innerHTML = renderInlineLoader('Memuat riwayat booking…');
        return;
      }
      const filtered = state.results.filter((r) => {
        const out = r.outcome || (r.is_dry_run ? 'DRY_RUN' : 'SUCCESS');
        if (state.histFilter === 'ALL') return true;
        if (state.histFilter === 'FAILED_GROUP') return out === 'FAILED' || out === 'NO_SLOT';
        return out === state.histFilter;
      });

      if (!filtered.length) {
        el.innerHTML = '<p class="muted small">Belum ada riwayat pada filter ini.</p>';
        return;
      }

      el.innerHTML = filtered.map((r) => {
        const out = r.outcome || (r.is_dry_run ? 'DRY_RUN' : 'SUCCESS');
        const tgBadge = r.telegram_sent ? ' · <i class="fa-brands fa-telegram"></i> Terkirim' : '';
        return `
          <div class="item-card">
            <div style="min-width:0">
              <div>
                <span class="out-pill out-${esc(out)}">${outcomeLabel(out)}</span>
                <b>${esc(r.slot_date)}</b> · ${esc(r.slot_label || r.slot_key)}
              </div>
              <div class="muted small">
                ${r.job_title ? `${esc(r.job_title)} · ` : ''}
                ${r.booking_ref ? `Ref: <code>${esc(r.booking_ref)}</code> · ` : ''}
                ${esc(r.created_at)}${tgBadge}
              </div>
              ${r.reason ? `<div class="muted small" style="margin-top:2px">${esc(r.reason)}</div>` : ''}
            </div>
            <button type="button" class="btn soft sm hist-detail" data-id="${r.id}">Detail</button>
          </div>
        `;
      }).join('');

      $$('.hist-detail', el).forEach((btn) => {
        btn.onclick = () => {
          const item = state.results.find((x) => String(x.id) === String(btn.dataset.id));
          if (!item) return;
          const out = item.outcome || (item.is_dry_run ? 'DRY_RUN' : 'SUCCESS');
          const html = `
            <dl class="detail-dl">
              <dt>Status</dt><dd><span class="out-pill out-${esc(out)}">${outcomeLabel(out)}</span></dd>
              <dt>Akun</dt><dd>${esc(item.account_name || item.account_ic)} (${esc(item.account_role || '-')})</dd>
              <dt>IC Number</dt><dd><code>${esc(item.account_ic)}</code></dd>
              <dt>Tempat Kerja</dt><dd>${esc(item.workplace || '-')}</dd>
              <dt>Tanggal</dt><dd><b>${esc(item.slot_date)}</b></dd>
              <dt>Jam Shift</dt><dd>${esc(item.slot_label || item.slot_key)} (<code>${esc(item.slot_key)}</code>)</dd>
              <dt>Seat</dt><dd>${esc(item.seat || '-')}</dd>
              <dt>Ref ID</dt><dd><code>${esc(item.booking_ref || '-')}</code></dd>
              <dt>Sumber Job</dt><dd>${esc(item.job_title || '-')} (${esc(item.mode || '-')})</dd>
              <dt>Waktu</dt><dd>${esc(item.created_at)}</dd>
              <dt>Detail Info</dt><dd>${esc(item.reason || '-')}</dd>
              <dt>Notif Telegram</dt><dd>${item.telegram_sent ? '<i class="fa-solid fa-circle-check"></i> Terkirim' : 'Tidak terkirim'} ${item.telegram_info ? `(${esc(item.telegram_info)})` : ''}</dd>
            </dl>
          `;
          openModal('Detail Riwayat Booking', html, null, 'OK', false, true);
        };
      });
    }

    $$('[data-hist-filter]').forEach((b) => {
      b.onclick = () => {
        state.histFilter = b.dataset.histFilter;
        $$('[data-hist-filter]').forEach((x) => x.classList.toggle('active', x === b));
        renderResults();
      };
    });

    $('#btnRefreshHistory').onclick = async () => {
      await Promise.all([loadJobs({ onUpdate: renderResults }), loadAvailability({ onUpdate: renderMyBookings }).catch(() => {})]);
      toast('Riwayat diperbarui.');
    };

    renderMyBookings();
    renderResults();
    loadAvailability({ onUpdate: renderMyBookings }).catch(() => {});
    loadJobs({ onUpdate: renderResults });
    return () => loadJobs({ onUpdate: renderResults });
  }

  /* ---------- PAGE: LOGS ---------- */
  function initLogsPage() {
    const qJobId = new URLSearchParams(window.location.search).get('job_id');
    if (qJobId) state.logJobId = qJobId;

    function ensureDefaultLogJob() {
      const validIds = new Set(state.jobs.map((j) => String(j.id)));
      if (state.logJobId === 'ALL' || state.logJobId === '0') return;
      if (state.logJobId && validIds.has(String(state.logJobId))) return;
      const live = state.jobs.find((j) => j.status === 'running' || j.status === 'waiting');
      if (live) state.logJobId = String(live.id);
      else if (state.jobs.length) state.logJobId = String(state.jobs[0].id);
      else state.logJobId = '0';
    }

    function renderLogSelectedBanner() {
      const banner = $('#logSelectedBanner');
      if (!banner) return;
      if (state.logJobId === 'ALL') {
        banner.classList.add('hidden');
        return;
      }
      if (state.logJobId === '0') {
        banner.innerHTML = '<div><b><i class="fa-solid fa-user-gear"></i> Aktivitas Akun Umum</b> · <span class="muted small">Log login, booking manual, dan tes Telegram.</span></div>';
        banner.classList.remove('hidden');
        return;
      }
      const job = state.jobs.find((j) => String(j.id) === String(state.logJobId));
      if (!job) {
        banner.classList.add('hidden');
        return;
      }
      const datesTxt = formatDatesLabel(job.target_dates);
      const slotsTxt = formatSlotsLabel(job.target_slots);
      const schedTxt = job.mode === 'scheduled'
        ? `Jam ${(job.schedule_time || '').slice(0, 5)} ${job.schedule_tz}${job.schedule_date ? ` (${job.schedule_date})` : ''}`
        : `Pantau tiap ${job.poll_interval}d`;
      banner.innerHTML = `
        <div>
          <b><i class="fa-solid fa-robot"></i> #${esc(job.id)} — ${esc(job.title)}</b>
          ${job.dry_run ? '<span class="badge">DRY-RUN</span>' : ''}
          <div class="muted small">${esc(schedTxt)} · Tanggal: ${esc(datesTxt)} · Jam: ${esc(slotsTxt)}</div>
        </div>
        <span class="st-pill st-${esc(job.status)}">${esc(job.status)}</span>
      `;
      banner.classList.remove('hidden');
    }

    function populateLogJobFilter() {
      ensureDefaultLogJob();
      const sel = $('#logJobFilter');
      if (sel) {
        sel.innerHTML = state.jobs.map((j) =>
          `<option value="${j.id}">#${j.id} — ${esc(j.title)} (${esc(j.status)})</option>`
        ).join('')
          + '<option value="0">Aktivitas Umum (Login/Manual)</option>'
          + '<option value="ALL">Semua Log</option>';
        sel.value = state.logJobId;
      }

      const tabsEl = $('#logSchedulerTabs');
      if (tabsEl) {
        tabsEl.innerHTML = state.jobs.map((j) => `
          <button type="button" class="log-sched-tab ${String(state.logJobId) === String(j.id) ? 'active' : ''}" data-log-job="${j.id}">
            <span class="st-dot ${esc(j.status)}"></span>
            <span>#${esc(j.id)} ${esc(j.title)}</span>
          </button>
        `).join('') + `
          <button type="button" class="log-sched-tab ${state.logJobId === '0' ? 'active' : ''}" data-log-job="0">
            <i class="fa-solid fa-user-gear"></i> <span>Aktivitas Umum</span>
          </button>
          <button type="button" class="log-sched-tab ${state.logJobId === 'ALL' ? 'active' : ''}" data-log-job="ALL">
            <i class="fa-solid fa-layer-group"></i> <span>Semua</span>
          </button>
        `;

        $$('.log-sched-tab', tabsEl).forEach((btn) => {
          btn.onclick = () => {
            state.logJobId = btn.dataset.logJob;
            if (sel) sel.value = state.logJobId;
            const url = new URL(window.location.href);
            url.searchParams.set('job_id', state.logJobId);
            window.history.replaceState({}, '', url.toString());
            populateLogJobFilter();
            loadLogs();
          };
        });
      }

      renderLogSelectedBanner();
    }

    async function loadLogs() {
      if (!state.ic) return;
      ensureDefaultLogJob();
      renderLogSelectedBanner();
      try {
        const jobId = state.logJobId;
        const level = $('#logLevelFilter').value;
        const qs = new URLSearchParams({ ic: state.ic, level, limit: '150' });
        if (jobId && jobId !== 'ALL') qs.set('job_id', jobId);
        const res = await api(`/api/logs?${qs.toString()}`);
        const box = $('#logsContainer');
        if (!box) return;
        if (!res.logs || !res.logs.length) {
          box.innerHTML = '<p class="muted">Belum ada log untuk scheduler ini.</p>';
          return;
        }
        box.innerHTML = res.logs.map((l, idx) => `
          <div class="log-row ${esc(l.level)}" style="align-items:center;">
            <span class="log-ts">[${esc(l.created_at)}]</span>
            <span class="log-lvl">${esc(l.level)}</span>
            ${(jobId === 'ALL' && l.job_title) ? `<span class="badge">#${esc(l.job_id)} ${esc(l.job_title)}</span>` : ''}
            <span style="flex:1; min-width:0;">${esc(l.message)}</span>
            <button type="button" class="btn soft sm btn-log-inspect" data-idx="${idx}" style="padding:3px 8px; font-size:11px; margin-left:auto;" title="Lihat JSON API & Analisa Respon Server"><i class="fa-solid fa-eye"></i> JSON</button>
          </div>
        `).join('');

        $$('.btn-log-inspect', box).forEach((btn) => {
          btn.onclick = async () => {
            const logItem = res.logs[Number(btn.dataset.idx)];
            await showApiInspectModal(logItem);
          };
        });
      } catch {
        // ignore if MySQL offline
      }
    }

    $('#logJobFilter').onchange = () => {
      state.logJobId = $('#logJobFilter').value;
      const url = new URL(window.location.href);
      url.searchParams.set('job_id', state.logJobId);
      window.history.replaceState({}, '', url.toString());
      populateLogJobFilter();
      loadLogs();
    };
    $('#logLevelFilter').onchange = loadLogs;
    $('#btnRefreshLogs').onclick = loadLogs;

    async function showApiInspectModal(logItem = null) {
      if (!state.ic) {
        toast('Silakan login terlebih dahulu.');
        return;
      }
      const topBtn = $('#btnInspectApi');
      if (topBtn) {
        topBtn.disabled = true;
        topBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Mengambil data…';
      }
      try {
        const selJob = state.jobs.find((j) => String(j.id) === String(state.logJobId));
        let targetDate = (selJob && selJob.target_dates && selJob.target_dates.length)
          ? selJob.target_dates[0]
          : '2026-12-14';
        if (logItem && logItem.message) {
          const matchDate = logItem.message.match(/2026-\d{2}-\d{2}/);
          if (matchDate) targetDate = matchDate[0];
        }

        const res = await api(`/api/inspect-raw?ic=${encodeURIComponent(state.ic)}&target_date=${encodeURIComponent(targetDate)}`);
        const jsonText = JSON.stringify(res, null, 2);
        const modalHtml = `
          <div style="font-size:13px; line-height:1.5;">
            ${logItem ? `<div style="background:var(--surface-2); border:1px solid var(--line); border-radius:8px; padding:10px; margin-bottom:12px;">
              <b>Log yang Diinspeksi:</b>
              <div class="muted small" style="margin-top:2px;">[${esc(logItem.created_at)}] <b>${esc(logItem.level)}</b>: ${esc(logItem.message)}</div>
            </div>` : ''}
            <div style="margin-bottom:10px; display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
              <span class="badge">Waktu Server: ${esc(res.server_time_myt)}</span>
              <span class="badge">Akun: ${esc(res.account?.name)} (${esc(res.account?.role)})</span>
              <span class="badge">Latensi: ${esc(res.latency_ms?.availability)}ms</span>
              <span class="badge">Total Hari Terbuka: ${esc(res.total_days)}</span>
            </div>
            ${res.target_day_json
              ? `<div style="background:rgba(59,130,246,0.1); border:1px solid rgba(59,130,246,0.3); border-radius:8px; padding:10px; margin-bottom:12px;">
                   <b>Status Target Tanggal (${esc(res.target_date)}):</b>
                   <pre style="margin:6px 0 0; background:none; padding:0; max-height:140px; overflow:auto; color:var(--text); font-family:monospace; font-size:12px;">${esc(JSON.stringify(res.target_day_json, null, 2))}</pre>
                 </div>`
              : `<div style="background:rgba(239,68,68,0.1); border:1px solid rgba(239,68,68,0.3); border-radius:8px; padding:10px; margin-bottom:12px; color:#ef4444;">
                   <b>Target Tanggal ${esc(res.target_date)} belum muncul di server</b> (artinya slot tanggal ini belum dibuka sebelum 00:00 MYT).
                 </div>`}
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
              <b>Format JSON Lengkap dari Server Locum:</b>
              <button type="button" class="btn soft sm" id="btnCopyJson"><i class="fa-solid fa-copy"></i> Salin JSON</button>
            </div>
            <pre id="rawJsonBox" style="background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:12px; max-height:360px; overflow:auto; font-family:monospace; font-size:11px; white-space:pre-wrap; word-break:break-all;">${esc(jsonText)}</pre>
          </div>
        `;
        openModal('Bukti JSON API Server Locum (Live Return)', modalHtml, null, 'Tutup', false, true);
        const copyBtn = $('#btnCopyJson');
        if (copyBtn) {
          copyBtn.onclick = () => {
            navigator.clipboard.writeText(jsonText).then(() => {
              toast('JSON berhasil disalin ke clipboard!');
            }).catch(() => {
              toast('Gagal menyalin otomatis.');
            });
          };
        }
      } catch (err) {
        toast(`Gagal memuat JSON API: ${err.message}`);
      } finally {
        if (topBtn) {
          topBtn.disabled = false;
          topBtn.innerHTML = '<i class="fa-solid fa-eye"></i> Cek JSON API Asli';
        }
      }
    }

    const btnInspect = $('#btnInspectApi');
    if (btnInspect) {
      btnInspect.onclick = () => showApiInspectModal(null);
    }

    populateLogJobFilter();
    loadJobs({
      onUpdate: () => {
        populateLogJobFilter();
        loadLogs();
      },
    });
    return () => {
      loadJobs({ onUpdate: populateLogJobFilter });
      loadLogs();
    };
  }

  /* ---------- PAGE: TELEGRAM ---------- */
  function initTelegramPage() {
    async function loadTelegramSettings() {
      if (!state.ic) return;
      try {
        const res = await api(`/api/telegram?ic=${encodeURIComponent(state.ic)}`);
        const s = res.settings || {};
        $('#tgToken').value = s.bot_token || '';
        $('#tgChatId').value = s.chat_id || '';
        $('#tgEnabled').checked = !!s.enabled;
        $('#tgSuccess').checked = s.notify_on_success !== false;
        $('#tgFail').checked = s.notify_on_fail !== false;
        $('#tgDry').checked = !!s.notify_on_dry_run;
      } catch {
        // ignore if MySQL offline
      }
    }

    $('#tgForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!state.ic) return;
      try {
        await api('/api/telegram', {
          method: 'POST',
          body: JSON.stringify({
            ic: state.ic,
            bot_token: $('#tgToken').value.trim(),
            chat_id: $('#tgChatId').value.trim(),
            enabled: $('#tgEnabled').checked,
            notify_on_success: $('#tgSuccess').checked,
            notify_on_fail: $('#tgFail').checked,
            notify_on_dry_run: $('#tgDry').checked,
          }),
        });
        toast('Konfigurasi Telegram disimpan ke MySQL!');
      } catch (err) {
        toast(`Gagal menyimpan: ${err.message}`);
      }
    });

    $('#btnTestTg').onclick = async () => {
      if (!state.ic) return;
      const btn = $('#btnTestTg');
      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Mengirim…';
      try {
        await api('/api/telegram/test', {
          method: 'POST',
          body: JSON.stringify({
            ic: state.ic,
            bot_token: $('#tgToken').value.trim(),
            chat_id: $('#tgChatId').value.trim(),
          }),
        });
        toast('Pesan tes berhasil dikirim ke Telegram!');
      } catch (err) {
        toast(`Gagal kirim tes: ${err.message}`);
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-paper-plane"></i> Tes Kirim Pesan';
      }
    };

    loadTelegramSettings();
    loadJobs();
  }

  /* ---------- PAGE: WATCHDOG ---------- */
  function initWatchdogPage() {
    async function refreshWatchdog() {
      try {
        const res = await api(`/api/watchdog-status?ic=${encodeURIComponent(state.ic || '')}`);
        const wd = res.watchdog || {};
        setText('#wdServiceState', res.db_ok ? `OK (PID ${res.pid})` : 'DB OFFLINE');
        setText('#wdThreadState', wd.watchdog_alive ? 'AKTIF (60d)' : 'MATI');
        setText('#wdChecksCount', String(wd.checks_count || 0));
        setText('#wdTzInfo', res.tz_env || 'MYT');

        const badge = $('#wdLiveBadge');
        if (badge) {
          badge.className = `st-pill ${wd.watchdog_alive && res.db_ok ? 'st-running' : 'st-error'}`;
          badge.innerHTML = wd.watchdog_alive
            ? '<i class="fa-solid fa-circle-dot"></i> Live'
            : '<i class="fa-solid fa-triangle-exclamation"></i> Cek Watchdog';
        }

        const banner = $('#wdMetaBanner');
        if (banner) {
          banner.innerHTML = `
            <div>
              <b><i class="fa-regular fa-clock"></i> Server MYT:</b> ${esc(res.server_time_myt)} ·
              <b>WIB:</b> ${esc(res.server_time_wib)}
              <div class="muted small">
                Cek watchdog terakhir: <b>${esc(wd.last_check_myt || '-')} MYT</b> (${esc(wd.last_check_wib || '-')} WIB) ·
                Interval: tiap ${esc(wd.interval_seconds || 60)} detik
              </div>
            </div>
            <span class="st-pill st-waiting">${(res.active_db_jobs || []).length} Job Aktif</span>
          `;
        }

        const jobsEl = $('#wdActiveJobsList');
        if (jobsEl) {
          const jobs = res.active_db_jobs || [];
          jobsEl.innerHTML = jobs.length
            ? jobs.map((j) => `
              <div class="item-card">
                <div style="min-width:0">
                  <div>
                    <span class="st-pill st-${esc(j.status)}">${esc(j.status)}</span>
                    <b>#${esc(j.id)} — ${esc(j.title)}</b>
                  </div>
                  <div class="muted small">
                    Jadwal: ${esc(j.schedule_date || '-')} ${esc((j.schedule_time || '').slice(0, 5))} ${esc(j.schedule_tz || 'MYT')}
                    · Target: <b>${esc(formatDatesLabel(j.target_dates))}</b>
                    · Jam Shift: <b>${esc(formatSlotsLabel(j.target_slots))}</b>
                  </div>
                  ${j.last_message ? `<div class="muted small">${esc(j.last_message)}</div>` : ''}
                </div>
                <span class="st-pill ${j.has_live_thread ? 'st-running' : 'st-error'}">
                  ${j.has_live_thread ? 'Thread Hidup' : 'Thread Mati'}
                </span>
              </div>
            `).join('')
            : '<p class="muted small">Tidak ada job berstatus waiting/running saat ini.</p>';
        }

        setText('#wdCronPath', res.cron_log_path || '/var/log/locumsync-watchdog.log');
        const cronBox = $('#wdCronLogsBox');
        if (cronBox) {
          const lines = res.cron_logs || [];
          if (!res.cron_log_exists) {
            cronBox.innerHTML = '<p class="muted small">File log cron Linux belum ada (normal jika dijalankan di Windows lokal; di VPS terisi otomatis tiap 1 menit).</p>';
          } else if (!lines.length) {
            cronBox.innerHTML = '<p class="muted small">Menunggu cek cron menit berikutnya…</p>';
          } else {
            cronBox.innerHTML = lines.map((line) => {
              const isErr = /unhealthy|error|restart|fail/i.test(line);
              return `<div class="log-row ${isErr ? 'WARN' : 'INFO'}"><span>${esc(line)}</span></div>`;
            }).join('');
          }
        }
      } catch {
        // ignore transient errors
      }
    }

    const btn = $('#btnRefreshWatchdog');
    if (btn) btn.onclick = () => { refreshWatchdog(); toast('Status monitor diperbarui.'); };

    refreshWatchdog();
    loadJobs();
    return () => { refreshWatchdog(); loadJobs(); };
  }

  /* ---------- Boot ---------- */
  checkHealth();
  tickClocks();
  setInterval(tickClocks, 1000);

  let pollHook = null;
  if (PAGE === 'login') {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem('locum_dash_user')); } catch { saved = null; }
    if (saved && saved.profile && saved.profile.ic) {
      window.location.replace('/jadwal');
      return;
    }
    initLoginPage();
  } else if (initAppShell()) {
    if (PAGE === 'jadwal') { initJadwalPage(); pollHook = () => loadJobs(); }
    else if (PAGE === 'auto_book') pollHook = initAutoBookPage();
    else if (PAGE === 'riwayat') pollHook = initRiwayatPage();
    else if (PAGE === 'logs') pollHook = initLogsPage();
    else if (PAGE === 'watchdog') pollHook = initWatchdogPage();
    else if (PAGE === 'telegram') { initTelegramPage(); pollHook = () => loadJobs(); }
  }

  setInterval(() => {
    checkHealth();
    if (state.ic && pollHook) pollHook();
  }, 3000);
})();

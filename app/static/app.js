/* Unclick mission control. Vanilla JS, renders the EventSink stream (see unclick/contracts.py). */
(() => {
  'use strict';

  const PHASES = [
    ['boot', 'Boot'], ['explore', 'Explore'], ['discover', 'Discover'], ['code', 'Code'],
    ['verify', 'Verify'], ['publish', 'Publish'], ['live', 'Live'],
  ];
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtMs = (ms) => (ms == null ? '' : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);
  const fmtSize = (b) => (b == null ? '' : b >= 1024 ? `${(b / 1024).toFixed(1)} KB` : `${b} B`);
  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });

  let S; // run state
  let es = null;
  let timerInt = null;
  let follow = true;
  let deskView = localStorage.getItem('unclick.deskView') || 'live';
  let config = { fake: false };

  function fresh() {
    return {
      runId: null, lastSeq: -1, t0: null, tEnd: null, phase: null, failed: false, done: false,
      sandboxes: new Map(), forks: [], pause: null,
      egress: null, captures: [], endpoint: null,
      codes: [], runs: [], verifies: [], selAttempt: null,
      publish: null, billing: { calls: null, last: null, feed: [], apiKey: null },
      liveUrl: null, shot: null, log: [], fake: undefined,
    };
  }

  // ---------------------------------------------------------------------------
  // Run lifecycle
  // ---------------------------------------------------------------------------

  async function startRun(e) {
    e && e.preventDefault();
    const url = $('urlIn').value.trim();
    const task = $('taskIn').value.trim();
    if (!url || !task) return toast('Enter a URL and a task');
    $('goBtn').disabled = true;
    try {
      const r = await fetch('/api/runs', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ url, task }) });
      if (!r.ok) throw new Error(await r.text());
      const j = await r.json();
      attach(j.run_id, j.fake, j);
    } catch (err) {
      toast('Could not start run: ' + err.message);
      $('goBtn').disabled = false;
    }
  }

  function attach(runId, fake, info) {
    if (es) es.close();
    S = fresh();
    S.runId = runId;
    S.fake = fake;
    if (info && info.mode) setRunMode(info);
    else fetch(`/api/runs/${runId}`).then((r) => r.json()).then((j) => { S.fake = j.fake; setRunMode(j); }).catch(() => {});
    history.replaceState(null, '', location.pathname + '#run=' + runId);
    try { localStorage.setItem('unclick.lastRun', runId); } catch { /* ignore */ }
    resetUI();
    $('runId').textContent = runId;
    $('goBtn').disabled = true;
    es = new EventSource(`/api/runs/${runId}/events`);
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.seq <= S.lastSeq) return;
      S.lastSeq = ev.seq;
      handle(ev);
    };
    es.onerror = async () => {
      // The server restarted or the run is unknown: stop retrying.
      const r = await fetch(`/api/runs/${runId}`).catch(() => null);
      if (!r || r.status === 404) {
        if (es) es.close();
        es = null;
        $('goBtn').disabled = false;
        if (r) { history.replaceState(null, '', location.pathname); toast('That run is gone (server restarted)'); }
      }
    };
    clearInterval(timerInt);
    timerInt = setInterval(tick, 250);
  }

  // Never let a local or replayed run pass as a live CreateOS run: the header badge says what is running.
  function setRunMode(info) {
    if (!S) return;
    S.mode = info.mode;
    S.replayOf = info.replay_of;
    S.recordedMode = info.recorded_mode;
    if (info.url && info.mode === 'replay') { $('urlIn').value = info.url; $('taskIn').value = info.task || $('taskIn').value; }
    renderModeBadge();
    renderRoles();
  }

  const MODE_LABEL = { local: 'LOCAL SANDBOX', createos: 'CREATEOS', fake: 'FAKE PIPELINE' };
  function renderModeBadge() {
    const b = $('modeBadge');
    const mode = (S && S.mode) || (config.fake ? 'fake' : config.sandbox) || 'createos';
    let text = MODE_LABEL[mode] || mode.toUpperCase();
    let tip = '';
    if (mode === 'replay') {
      const rec = S && S.recordedMode ? (MODE_LABEL[S.recordedMode] || S.recordedMode.toUpperCase()) : 'RECORDED';
      text = `REPLAY · ${rec}`;
      tip = `Replaying the recording ${S && S.replayOf ? S.replayOf : ''} (not a live run)`;
    } else if (mode === 'local') {
      tip = 'Local sandbox mode: real agent + local Chrome + local processes. No CreateOS sandboxes; egress, fork and auto-pause are not enforced.';
    } else if (!config.pipeline && !config.fake) text = 'NO PIPELINE · FAKE';
    const warn = mode !== 'createos';
    b.textContent = text;
    b.title = tip;
    b.className = 'chip font-bold tracking-wider ' + (warn ? 'bg-amber-500/15 text-amber-300 border-amber-500/40' : 'bg-ink-800 border-ink-600 text-slate-400');
    b.classList.toggle('hidden', mode === 'createos' && !tip);
  }

  function stripeOff() { return !config.stripe && !config.fake && !(S && S.fake); }
  function renderBillingMode() {
    const off = stripeOff();
    const btn = $('buyBtn');
    btn.disabled = off;
    btn.title = off ? 'Stripe test mode is not configured (no STRIPE_SECRET_KEY). The published API runs on its demo key.' : '';
    btn.classList.toggle('opacity-40', off);
    btn.classList.toggle('cursor-not-allowed', off);
    const chip = $('stripeModeChip');
    if (chip) {
      chip.textContent = off ? 'demo key' : 'test mode';
      chip.title = off ? 'Stripe test mode: not configured' : 'Stripe test mode';
    }
    if (off && S && !S.billing.last) {
      const lc = $('lastCharge');
      lc.className = 'rounded-xl bg-amber-500/5 border border-amber-500/30 px-3 py-2 text-[13px] text-amber-200';
      lc.textContent = 'Stripe test mode: not configured. Demo key active';
    }
  }

  function tick() {
    if (!S || !S.t0) return;
    const end = S.tEnd || Date.now() / 1000;
    const s = Math.max(0, Math.floor(end - S.t0));
    $('timer').textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  }

  // ---------------------------------------------------------------------------
  // Event dispatch
  // ---------------------------------------------------------------------------

  function handle(ev) {
    const d = ev.data || {};
    if (S.t0 == null) { S.t0 = ev.ts; $('runDot').className = 'w-2 h-2 rounded-full bg-brand-400 blink'; }
    if (ev.kind !== 'screenshot') addLog(ev);
    switch (ev.kind) {
      case 'status': onStatus(d, ev); break;
      case 'sandbox': onSandbox(d, ev); break;
      case 'live_view': onLiveView(d); break;
      case 'egress': onEgress(d, ev); break;
      case 'browser_action': onAction(d, ev); break;
      case 'screenshot': onShot(d); break;
      case 'capture': onCapture(d, ev); break;
      case 'endpoint': onEndpoint(d, ev); break;
      case 'code': onCode(d, ev); break;
      case 'run': onRun(d, ev); break;
      case 'verify': onVerify(d, ev); break;
      case 'publish': onPublish(d, ev); break;
      case 'billing': onBilling(d, ev); break;
      case 'pause': onPause(d, ev); break;
      case 'error': toast('⚠ ' + ev.message, 5000); break;
      case 'done': onDone(d, ev); break;
      default: break;
    }
  }

  // ---------------------------------------------------------------------------
  // Stepper + focus
  // ---------------------------------------------------------------------------

  function onStatus(d) {
    if (d.phase === 'failed') { S.failed = true; renderStepper(); return; }
    if (!PHASES.some(([k]) => k === d.phase)) return;
    S.phase = d.phase;
    renderStepper();
    setFocus();
  }

  function setFocus() {
    const p = S && S.phase;
    const cls = ['code', 'verify'].includes(p) ? 'focus-code' : ['publish', 'live'].includes(p) ? 'focus-live' : 'focus-explore';
    document.body.classList.remove('focus-code', 'focus-live', 'focus-explore');
    document.body.classList.add(cls);
  }

  function renderStepper() {
    const cur = S && S.phase ? PHASES.findIndex(([k]) => k === S.phase) : -1;
    const attempts = S ? S.codes.length : 0;
    const parts = [];
    PHASES.forEach(([k, label], i) => {
      let cls = 'step';
      const allDone = S && S.done && !S.failed;
      if (allDone || i < cur) cls += ' done';
      else if (i === cur) cls += S.failed ? ' failed' : ' active';
      const icon = cls.includes('done') ? '✓' : cls.includes('failed') ? '✕' : String(i + 1);
      let extra = '';
      if (k === 'code' && attempts > 1) extra = `<span class="chip bg-amber-500/15 text-amber-300 border-amber-500/30">attempt ${attempts}</span>`;
      if (k === 'live' && S && S.publish) extra = `<span class="live-dot"></span>`;
      parts.push(`<div class="${cls}"><span class="dot">${icon}</span><span>${label}</span>${extra}</div>`);
      if (i < PHASES.length - 1) {
        const lc = allDone || i < cur ? 'done' : i === cur && !S.failed ? 'active' : '';
        parts.push(`<div class="step-line ${lc}"></div>`);
      }
    });
    $('stepper').innerHTML = parts.join('');
  }

  // ---------------------------------------------------------------------------
  // Desktop
  // ---------------------------------------------------------------------------

  function noVncUrl(u) {
    // noVNC: scale the remote screen to the iframe and connect without a click.
    try {
      const x = new URL(u, location.href);
      if (/vnc(_lite)?\.html$/.test(x.pathname)) {
        if (!x.searchParams.has('resize')) x.searchParams.set('resize', 'scale');
        if (!x.searchParams.has('autoconnect')) x.searchParams.set('autoconnect', 'true');
        return x.toString();
      }
    } catch { /* keep as is */ }
    return u;
  }

  function onLiveView(d) {
    S.liveUrl = noVncUrl(d.url);
    $('liveFrame').src = S.liveUrl;
    const pop = $('popOut');
    pop.href = S.liveUrl;
    pop.classList.remove('hidden');
    $('liveDot').classList.remove('hidden');
    $('deskLabel').textContent = 'LIVE DESKTOP';
    renderDesk();
  }

  function onShot(d) {
    if (!d.png_b64) return;
    S.shot = 'data:image/png;base64,' + d.png_b64;
    $('shotImg').src = S.shot;
    renderDesk();
  }

  function renderDesk() {
    const haveLive = !!(S && S.liveUrl);
    const haveShot = !!(S && S.shot);
    const view = deskView === 'live' && haveLive ? 'live' : haveShot ? 'shot' : haveLive ? 'live' : null;
    $('liveFrame').classList.toggle('hidden', view !== 'live');
    $('shotImg').classList.toggle('hidden', view !== 'shot');
    $('deskEmpty').classList.toggle('hidden', !!view);
    document.querySelectorAll('.dv').forEach((b) => {
      const on = b.dataset.view === (view || deskView);
      b.className = 'dv px-2 py-0.5 rounded-md ' + (on ? 'text-white bg-white/15' : 'text-slate-400');
    });
  }

  const ACTION_ICON = { open: '🌐', click: '👆', type: '⌨️', press: '⏎', scroll: '↕', wait: '⏳' };
  function onAction(d, ev) {
    const t = $('ticker');
    const el = document.createElement('div');
    el.className = 'ticker-item flex items-center gap-2 rounded-lg bg-black/70 border border-white/10 px-3 py-1.5 text-[13px] max-w-full';
    el.innerHTML = `<span class="text-base leading-none">${ACTION_ICON[d.action] || '•'}</span>
      <span class="font-bold uppercase text-[11px] tracking-wider text-brand-300">${esc(d.action)}</span>
      <span class="text-white truncate kbd text-[12.5px]">${esc(d.detail || ev.message)}</span>`;
    t.appendChild(el);
    while (t.children.length > 4) t.removeChild(t.firstChild);
    [...t.children].forEach((c, i, arr) => { c.style.opacity = String(0.35 + 0.65 * ((i + 1) / arr.length)); });
  }

  // ---------------------------------------------------------------------------
  // Network captures + endpoint
  // ---------------------------------------------------------------------------

  function splitUrl(u) {
    try { const x = new URL(u); return { host: x.host, path: x.pathname + x.search }; } catch { return { host: '', path: u }; }
  }

  function onCapture(d) {
    S.captures.push(d);
    $('capEmpty') && $('capEmpty').remove();
    $('capCount').textContent = S.captures.length;
    const { host, path } = splitUrl(d.url);
    const ok = d.status >= 200 && d.status < 300;
    const el = document.createElement('div');
    el.className = 'cap anim-in flex items-center gap-2 rounded-lg bg-ink-800/70 border border-ink-700 px-2.5 py-1.5 text-[12.5px]';
    el.dataset.url = d.url;
    el.innerHTML = `<span class="chip bg-sky-500/15 text-sky-300 border-sky-500/30 kbd">${esc(d.method || 'GET')}</span>
      <span class="chip kbd ${ok ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30' : 'bg-red-500/10 text-red-300 border-red-500/30'}">${esc(d.status)}</span>
      <span class="truncate kbd min-w-0 flex-1"><span class="text-white font-semibold">${esc(host)}</span><span class="text-slate-400">${esc(path)}</span></span>
      <span class="text-slate-500 kbd shrink-0">${esc(fmtSize(d.size))}</span>`;
    $('capList').appendChild(el);
    $('capList').scrollTop = $('capList').scrollHeight;
    if (S.endpoint) markPicked();
  }

  function onEndpoint(d) {
    S.endpoint = d;
    const { host, path } = splitUrl(d.url);
    const box = $('endpointBox');
    box.classList.remove('hidden');
    box.innerHTML = `<div class="flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-brand-300">
        <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M20 6 9 17l-5-5"/></svg> Hidden API found</div>
      <div class="kbd text-[14px] mt-1.5 break-all"><span class="chip bg-sky-500/15 text-sky-300 border-sky-500/30 mr-1">${esc(d.method || 'GET')}</span><span class="text-white font-bold">${esc(host)}</span><span class="text-slate-300">${esc(path)}</span></div>
      <div class="text-[13px] text-slate-300 mt-1.5 leading-snug">${esc(d.why || '')}</div>`;
    markPicked();
  }

  function markPicked() {
    document.querySelectorAll('.cap').forEach((el) => {
      const hit = el.dataset.url === S.endpoint.url;
      el.classList.toggle('border-brand-500/60', hit);
      el.classList.toggle('bg-brand-500/10', hit);
      el.classList.toggle('opacity-50', !hit);
    });
  }

  // ---------------------------------------------------------------------------
  // Egress
  // ---------------------------------------------------------------------------

  function onEgress(d) {
    const prev = S.egress ? S.egress.allow : [];
    const allow = d.allow || [];
    const added = d.added || allow.filter((h) => !prev.includes(h));
    const removed = prev.filter((h) => !allow.includes(h));
    S.egress = { allow, added, removed, reason: d.reason };
    const hosts = $('egressHosts');
    const rows = allow.map((h) => {
      const isNew = added.includes(h) && prev.length > 0;
      const first = added.includes(h) && prev.length === 0;
      return `<div class="${isNew ? 'host-new' : first ? 'anim-in' : ''} flex items-center gap-2 rounded-lg px-2.5 py-1.5 bg-ink-800/70 border border-ink-700">
        <svg class="w-4 h-4 ${isNew ? 'text-brand-300' : 'text-amber-300'} shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>
        <span class="kbd text-[14px] ${isNew ? 'text-brand-200 font-bold' : 'text-white'} truncate">${esc(h)}</span>
        <div class="flex-1"></div>
        ${isNew ? '<span class="chip bg-brand-500 text-brand-ink font-bold">+ added</span>' : '<span class="chip bg-ink-700 text-slate-400">allowed</span>'}
      </div>`;
    });
    removed.forEach((h) => rows.push(`<div class="anim-in flex items-center gap-2 rounded-lg px-2.5 py-1.5 border border-red-500/25" style="animation: flashRed 2s ease-out both">
        <svg class="w-4 h-4 text-red-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M5 5l14 14"/></svg>
        <span class="kbd text-[14px] text-red-300/80 line-through truncate">${esc(h)}</span>
        <div class="flex-1"></div><span class="chip bg-red-500/15 text-red-300 border-red-500/30">removed</span></div>`));
    if (!allow.length) rows.push('<div class="chip bg-red-500/15 text-red-300 border-red-500/30">OPEN: no allowlist</div>');
    hosts.innerHTML = rows.join('');
    $('egressReason').innerHTML = d.reason ? `<span class="text-slate-500">Why:</span> ${esc(d.reason)}` : '';
    const mode = $('egressMode');
    mode.textContent = allow.length ? `deny-by-default · ${allow.length} host${allow.length > 1 ? 's' : ''}` : 'open';
    mode.className = 'chip normal-case tracking-normal ' + (allow.length ? 'bg-amber-500/10 text-amber-300 border-amber-500/30' : 'bg-red-500/10 text-red-300 border-red-500/30');
  }

  // ---------------------------------------------------------------------------
  // Sandboxes, fork, pause
  // ---------------------------------------------------------------------------

  const ROLE = {
    desktop: { label: 'Desktop', sub: 'desktop:1 · Chrome', color: 'sky' },
    runner: { label: 'Runner', sub: 'devbox:1 · runs client.py', color: 'violet' },
    api: { label: 'Public API', sub: 'ingress · auto-pause', color: 'brand' },
  };
  const ROLE_SUB = { desktop: 'desktop:1 · Chrome', runner: 'devbox:1 · runs client.py', api: 'ingress · auto-pause' };
  const ROLE_SUB_LOCAL = { desktop: 'local process · local Chrome', runner: 'local process · runs client.py', api: 'local process · 127.0.0.1' };
  function isLocal() { return S && (S.mode === 'local' || (S.mode === 'replay' && S.recordedMode === 'local')); }
  function renderRoles() {
    const subs = isLocal() ? ROLE_SUB_LOCAL : ROLE_SUB;
    Object.keys(ROLE).forEach((k) => { ROLE[k].sub = subs[k]; });
    if (S && S.sandboxes.size) renderSandboxes();
    $('deskLabel').textContent = S && S.liveUrl ? 'LIVE DESKTOP' : isLocal() ? 'LOCAL CHROME' : 'DESKTOP';
    if ($('sbTitle')) $('sbTitle').textContent = isLocal() ? 'Local sandboxes (no CreateOS)' : 'CreateOS Sandboxes';
    if ($('deskEmptyTitle')) $('deskEmptyTitle').textContent = isLocal() || (!S.mode && config.sandbox === 'local') ? 'Local Chrome (local sandbox mode)' : 'CreateOS desktop sandbox';
  }
  const STATE = {
    created: ['Running', 'emerald'], forked: ['Running', 'emerald'], resumed: ['Running', 'emerald'],
    pausing: ['Pausing…', 'amber'], paused: ['Paused', 'amber'], destroyed: ['Destroyed', 'slate'],
  };

  function onSandbox(d) {
    const prev = S.sandboxes.get(d.id) || {};
    const sb = { ...prev, id: d.id, role: d.role || prev.role, action: d.action };
    if (d.ms != null && ['created', 'forked'].includes(d.action)) sb.bootMs = d.ms;
    if (d.forked_from) sb.forkedFrom = d.forked_from;
    S.sandboxes.set(d.id, sb);
    if (d.action === 'forked' && d.forked_from) S.forks.push({ from: d.forked_from, to: d.id, ms: d.ms });
    renderSandboxes();
  }

  function renderSandboxes() {
    const list = [...S.sandboxes.values()];
    $('sbCount').textContent = list.filter((s) => s.action !== 'destroyed').length;
    $('sbList').innerHTML = list.map((s) => {
      const r = ROLE[s.role] || { label: s.role || 'sandbox', sub: '', color: 'slate' };
      const [st, c] = STATE[s.action] || [s.action, 'slate'];
      const dead = s.action === 'destroyed';
      if (dead) {
        return `<div class="flex items-center gap-2 rounded-lg px-3 py-1 bg-ink-800/40 border border-ink-700/60 opacity-60 text-[12px]">
          <span class="text-slate-400 font-semibold">${esc(r.label)}</span><span class="kbd text-slate-500 truncate">${esc(s.id)}</span>
          <div class="flex-1"></div><span class="text-slate-500">destroyed · slot freed</span></div>`;
      }
      return `<div class="anim-in flex items-center gap-2.5 rounded-xl px-3 py-2 bg-ink-800/70 border border-ink-700 ${dead ? 'opacity-45' : ''}">
        <div class="w-8 h-8 rounded-lg bg-${r.color}-500/15 text-${r.color}-300 grid place-items-center shrink-0">${roleIcon(s.role)}</div>
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2"><span class="font-semibold text-white text-[14px]">${esc(r.label)}</span><span class="kbd text-[12px] text-slate-400 truncate">${esc(s.id)}</span></div>
          <div class="text-[11.5px] text-slate-500 truncate">${esc(r.sub)}${s.bootMs ? ` · ${s.forkedFrom ? 'forked' : 'up'} in ${fmtMs(s.bootMs)}` : ''}</div>
        </div>
        <span class="chip bg-${c}-500/15 text-${c}-300 border-${c}-500/30">${s.action === 'pausing' ? '<span class="blink">●</span>' : '●'} ${esc(st)}</span>
      </div>`;
    }).join('') || '<div class="text-slate-600 text-sm">No sandboxes yet</div>';

    const fb = $('forkBox');
    if (S.forks.length) {
      const f = S.forks[S.forks.length - 1];
      const from = S.sandboxes.get(f.from) || { role: 'runner', id: f.from };
      const to = S.sandboxes.get(f.to) || { role: 'api', id: f.to };
      fb.classList.remove('hidden');
      fb.innerHTML = `<div class="rounded-xl border border-brand-500/30 bg-brand-500/5 px-3 py-2.5">
        <div class="text-[11px] font-bold uppercase tracking-wider text-brand-300 mb-1.5">Fork · same warm state, tighter policy</div>
        <div class="flex items-center gap-2">
          <div class="text-center min-w-0"><div class="text-[12px] font-semibold text-white">${esc((ROLE[from.role] || {}).label || from.role)}</div><div class="kbd text-[11px] text-slate-500 truncate">${esc(f.from)}</div></div>
          <svg class="flex-1 h-6 min-w-[60px]" viewBox="0 0 100 24" preserveAspectRatio="none"><path class="fork-path" d="M2 12 H90" stroke-width="2" fill="none"/><path class="fork-head" d="M88 6 L98 12 L88 18" stroke-width="2.2" fill="none"/></svg>
          <div class="text-center min-w-0"><div class="text-[12px] font-semibold text-white">${esc((ROLE[to.role] || {}).label || to.role)}</div><div class="kbd text-[11px] text-slate-500 truncate">${esc(f.to)}</div></div>
        </div>
        ${f.ms ? `<div class="text-center text-[11.5px] text-brand-300/80 mt-1">memory + disk copied in ${fmtMs(f.ms)}</div>` : ''}
      </div>`;
    }
  }

  function roleIcon(role) {
    if (role === 'desktop') return '<svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>';
    if (role === 'api') return '<svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>';
    return '<svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m5 7 5 5-5 5M12 19h7"/></svg>';
  }

  function onPause(d) {
    S.pause = { state: d.state, wake_ms: d.wake_ms ?? (S.pause && d.state !== 'paused' ? S.pause.wake_ms : null) };
    renderPause();
    if (d.state === 'awake' && d.wake_ms != null) showWake(d.wake_ms);
  }

  function renderPause() {
    const p = S.pause;
    const box = $('pauseBox');
    if (!p) { box.classList.add('hidden'); return; }
    box.classList.remove('hidden');
    const cfg = {
      paused: ['amber', '<svg class="w-6 h-6" viewBox="0 0 24 24" fill="currentColor"><path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z"/></svg>', 'Paused', 'Idle → auto-paused. Costs nothing until the next request.'],
      waking: ['sky', '<svg class="w-6 h-6 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M21 12a9 9 0 1 1-6.2-8.6"/></svg>', 'Waking…', 'Request arrived: resuming on demand'],
      awake: ['emerald', '<svg class="w-6 h-6" viewBox="0 0 24 24" fill="currentColor"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z"/></svg>', 'Awake', 'Wake-on-request'],
    }[p.state] || ['slate', '', p.state, ''];
    const [c, icon, label, sub] = cfg;
    box.innerHTML = `<div class="anim-in flex items-center gap-3 rounded-xl px-3 py-2.5 bg-${c}-500/10 border border-${c}-500/35">
      <div class="text-${c}-300">${icon}</div>
      <div class="flex-1 min-w-0"><div class="font-bold text-${c}-200 text-[15px]">API ${label}</div><div class="text-[11.5px] text-slate-400 truncate">${sub}</div></div>
      ${p.state === 'awake' && p.wake_ms != null ? `<div class="text-right shrink-0"><div class="text-[22px] font-extrabold text-white tabular-nums leading-none">${fmtMs(p.wake_ms)}</div><div class="text-[10.5px] uppercase tracking-wider text-slate-500 mt-0.5">wake latency</div></div>` : ''}
    </div>`;
    const sc = $('sbScroll');
    sc.scrollTop = sc.scrollHeight;
  }

  // ---------------------------------------------------------------------------
  // Code / runs / verify
  // ---------------------------------------------------------------------------

  function onCode(d) {
    S.codes = S.codes.filter((c) => c.attempt !== d.attempt);
    S.codes.push(d);
    S.codes.sort((a, b) => a.attempt - b.attempt);
    S.selAttempt = d.attempt;
    $('codeCount').textContent = S.codes.length;
    renderPills();
    renderCode(true);
    renderStepper();
  }

  function attemptState(n) {
    const v = S.verifies.filter((x) => x.attempt === n).pop();
    if (v) return v.ok ? 'ok' : 'fail';
    const r = S.runs.filter((x) => x.attempt === n).pop();
    if (r) return r.exit_code === 0 ? 'ran' : 'fail';
    return 'pending';
  }

  function renderPills() {
    $('attemptPills').innerHTML = S.codes.map((c) => {
      const st = attemptState(c.attempt);
      const sel = c.attempt === S.selAttempt;
      const col = st === 'ok' ? 'emerald' : st === 'fail' ? 'red' : st === 'ran' ? 'sky' : 'slate';
      const mark = st === 'ok' ? '✓' : st === 'fail' ? '✕' : st === 'ran' ? '▶' : '…';
      return `<button data-attempt="${c.attempt}" class="chip ${sel ? `bg-${col}-500/25 text-white border-${col}-400/70` : `bg-${col}-500/10 text-${col}-300 border-${col}-500/25`}">${mark} Attempt ${c.attempt}</button>`;
    }).join('');
  }

  function hlLines(code) {
    let html;
    try { html = hljs.highlight(code, { language: 'python', ignoreIllegals: true }).value; } catch { html = esc(code); }
    const out = []; const stack = []; let cur = '';
    for (const tok of html.split(/(<span[^>]*>|<\/span>|\n)/)) {
      if (!tok) continue;
      if (tok === '\n') { out.push(cur + '</span>'.repeat(stack.length)); cur = stack.join(''); }
      else if (tok.startsWith('<span')) { stack.push(tok); cur += tok; }
      else if (tok === '</span>') { stack.pop(); cur += tok; }
      else cur += tok;
    }
    out.push(cur + '</span>'.repeat(stack.length));
    return out;
  }

  function renderCode(animate) {
    const idx = S.codes.findIndex((c) => c.attempt === S.selAttempt);
    const cur = S.codes[idx];
    if (!cur) return;
    const prev = idx > 0 ? S.codes[idx - 1] : null;
    const norm = (s) => String(s || '').replace(/\r\n/g, '\n').replace(/\n+$/, '');
    const curCode = norm(cur.code);
    const newHL = hlLines(curCode);
    const rows = [];
    let added = 0, removed = 0, firstChange = -1;
    if (!prev) {
      newHL.forEach((h, i) => rows.push(line('', i + 1, h, animate ? i : -1)));
    } else {
      const prevCode = norm(prev.code);
      const oldHL = hlLines(prevCode);
      const parts = Diff.diffLines(prevCode + '\n', curCode + '\n');
      let ni = 0, oi = 0;
      for (const p of parts) {
        const n = p.value.replace(/\n$/, '').split('\n').length;
        for (let k = 0; k < n; k++) {
          if (p.added) { if (firstChange < 0) firstChange = rows.length; rows.push(line('add', ni + 1, newHL[ni] ?? '', animate ? added : -1)); ni++; added++; }
          else if (p.removed) { if (firstChange < 0) firstChange = rows.length; rows.push(line('del', '', oldHL[oi] ?? '', -1)); oi++; removed++; }
          else { rows.push(line('', ni + 1, newHL[ni] ?? '', -1)); ni++; oi++; }
        }
      }
    }
    const view = $('codeView');
    view.innerHTML = rows.join('');
    const diffTxt = prev ? ` · <span class="text-emerald-400">+${added}</span> <span class="text-red-400">−${removed}</span> vs attempt ${prev.attempt}` : '';
    $('codeMeta').innerHTML = `<span class="kbd text-slate-300">${esc(cur.filename || 'client.py')}</span> · ${curCode.split('\n').length} lines${diffTxt}`;
    if (firstChange >= 0) {
      const el = view.children[firstChange];
      if (el) view.scrollTop = Math.max(0, el.offsetTop - view.clientHeight * 0.3);
    } else view.scrollTop = 0;
  }

  function line(kind, n, html, delayIdx) {
    const gut = kind === 'add' ? '+' : kind === 'del' ? '−' : '';
    const style = delayIdx >= 0 ? ` style="animation: slideIn .25s ease-out both; animation-delay:${Math.min(delayIdx * 28, 1600)}ms"` : '';
    return `<div class="code-line ${kind}"${style}><span class="ln">${n}</span><span class="gut">${gut}</span><span class="src">${html || ' '}</span></div>`;
  }

  function clip(s, max = 60) {
    const lines = String(s || '').split('\n');
    return lines.length > max ? lines.slice(0, max).join('\n') + `\n… (${lines.length - max} more lines)` : String(s || '');
  }

  function onRun(d) {
    S.runs.push(d);
    $('runCount').textContent = S.runs.length;
    const ok = d.exit_code === 0;
    const el = document.createElement('div');
    el.className = 'anim-in rounded-xl border ' + (ok ? 'border-emerald-500/35' : 'border-red-500/40') + ' bg-ink-900 overflow-hidden';
    el.innerHTML = `<div class="flex items-center gap-2 px-3 py-2 ${ok ? 'bg-emerald-500/10' : 'bg-red-500/10'}">
        <span class="kbd text-[12.5px] text-slate-300">$ python client.py</span>
        <span class="text-[12px] text-slate-500">attempt ${esc(d.attempt)}</span>
        <div class="flex-1"></div>
        <span class="text-[12px] text-slate-400 kbd">${esc(fmtMs(d.ms))}</span>
        <span class="chip ${ok ? 'bg-emerald-500 text-ink-950' : 'bg-red-500 text-white'} font-bold">exit ${esc(d.exit_code)}</span>
      </div>
      ${d.stderr ? `<pre class="term text-red-300 px-3 py-2 bg-red-500/5 border-t border-red-500/20">${esc(clip(d.stderr, 40))}</pre>` : ''}
      ${d.stdout ? `<pre class="term text-slate-200 px-3 py-2 border-t border-ink-700">${esc(clip(d.stdout, 60))}</pre>` : ''}
      ${!d.stdout && !d.stderr ? '<div class="term text-slate-600 px-3 py-2">(no output)</div>' : ''}`;
    const pane = $('pane-runs');
    if (S.runs.length === 1) pane.innerHTML = '';
    pane.appendChild(el);
    if (follow) showRTab('runs');
    // Show the start of the newest run's output
    pane.scrollTop = el.offsetTop - 8;
    renderPills();
  }

  function onVerify(d) {
    S.verifies.push(d);
    $('verifyCount').textContent = S.verifies.length;
    const el = document.createElement('div');
    el.className = 'anim-in rounded-xl border ' + (d.ok ? 'border-emerald-500/40' : 'border-red-500/40') + ' bg-ink-900 overflow-hidden';
    el.innerHTML = `<div class="flex items-center gap-2 px-3 py-2 ${d.ok ? 'bg-emerald-500/10' : 'bg-red-500/10'}">
        <span class="chip ${d.ok ? 'bg-emerald-500 text-ink-950' : 'bg-red-500 text-white'} font-bold">${d.ok ? '✓ MATCH' : '✕ MISMATCH'}</span>
        <span class="text-[12.5px] text-slate-400">attempt ${esc(d.attempt)}</span>
      </div>
      <div class="grid grid-cols-2 gap-px bg-ink-700">
        <div class="bg-ink-900 p-2.5 min-w-0"><div class="text-[10.5px] uppercase tracking-wider font-bold text-slate-500 mb-1">Expected · from the screen</div><pre class="term text-slate-200">${esc(clip(d.expected, 14))}</pre></div>
        <div class="bg-ink-900 p-2.5 min-w-0"><div class="text-[10.5px] uppercase tracking-wider font-bold ${d.ok ? 'text-emerald-400' : 'text-red-400'} mb-1">Got · from client.py</div><pre class="term ${d.ok ? 'text-emerald-200' : 'text-red-300'}">${esc(clip(d.got, 14))}</pre></div>
      </div>
      ${d.notes ? `<div class="px-3 py-2 text-[13px] text-slate-300 border-t border-ink-700"><span class="text-slate-500">Agent:</span> ${esc(d.notes)}</div>` : ''}`;
    const pane = $('pane-verify');
    if (S.verifies.length === 1) pane.innerHTML = '';
    pane.appendChild(el);
    if (follow) showRTab('verify');
    pane.scrollTop = el.offsetTop - 8;
    renderPills();
  }

  // ---------------------------------------------------------------------------
  // Publish / API card / billing
  // ---------------------------------------------------------------------------

  function onPublish(d) {
    S.publish = d;
    $('netPanel').classList.add('hidden');
    const card = $('apiCard');
    card.classList.remove('hidden');
    card.innerHTML = `
      <div class="flex items-center gap-2 px-4 pt-3.5">
        <span class="live-dot"></span>
        <span class="text-[12px] font-extrabold tracking-[.14em] text-brand-300">LIVE API</span>
        <span class="chip bg-ink-800 border-ink-600 text-slate-400 kbd">${esc(d.sandbox_id || '')}</span>
        <div class="flex-1"></div>
        ${d.docs_url ? `<a href="${esc(d.docs_url)}" target="_blank" rel="noopener" class="text-[12px] text-slate-400 hover:text-white underline underline-offset-2">OpenAPI docs ↗</a>` : ''}
      </div>
      <div class="px-4 pt-2 flex items-center gap-2">
        <div class="kbd text-[19px] font-bold text-white break-all leading-snug flex-1 min-w-0">${esc(d.api_url)}</div>
        <button data-copy="url" class="shrink-0 h-8 px-2.5 rounded-lg bg-ink-700 hover:bg-ink-600 text-[12px] font-semibold">Copy</button>
      </div>
      <div class="mx-4 mt-2.5 rounded-xl bg-black/50 border border-ink-700 px-3 py-2 flex items-start gap-2">
        <span class="kbd text-brand-400 text-[13px] select-none">$</span>
        <code class="kbd text-[12.5px] text-slate-200 break-all flex-1 min-w-0">${esc(d.curl || '')}</code>
        <button data-copy="curl" class="shrink-0 h-7 px-2.5 rounded-lg bg-ink-700 hover:bg-ink-600 text-[12px] font-semibold">Copy curl</button>
      </div>
      <div class="px-4 pt-3 flex items-center gap-2">
        <button id="tryBtn" class="h-10 px-4 rounded-xl bg-brand-500 hover:bg-brand-400 text-brand-ink font-bold text-sm flex items-center gap-2">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><path d="M7 4v16l13-8z"/></svg>Try it</button>
        <button id="wakeBtn" class="h-10 px-4 rounded-xl bg-sky-500/15 hover:bg-sky-500/25 border border-sky-500/40 text-sky-200 font-bold text-sm flex items-center gap-2">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z"/></svg>Wake test</button>
        <div class="flex-1"></div>
        <div id="tryMeta" class="flex items-center gap-2"></div>
      </div>
      <div class="flex-1 min-h-0 mx-4 mt-3 mb-4 rounded-xl bg-black/40 border border-ink-700 overflow-auto scroll-thin">
        <pre id="tryOut" class="term text-[12.5px] p-3 text-slate-500">Press “Try it” to call the API. No LLM in the hot path, just the generated client.</pre>
      </div>`;
    startStatusPoll();
    card.querySelector('#tryBtn').onclick = tryIt;
    card.querySelector('#wakeBtn').onclick = wakeTest;
    card.querySelectorAll('[data-copy]').forEach((b) => {
      b.onclick = () => copy(b.dataset.copy === 'url' ? d.api_url : d.curl, b);
    });
    renderStepper();
  }

  async function tryIt() {
    const btn = $('tryBtn');
    btn.disabled = true;
    $('tryOut').className = 'term text-[12.5px] p-3 text-slate-500';
    $('tryOut').textContent = 'GET ' + S.publish.api_url + ' …';
    try {
      const r = await fetch('/api/try', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ run_id: S.runId, api_url: S.publish.api_url, key: boughtKey() }) });
      const j = await r.json();
      if (j.key_rejected) { try { localStorage.removeItem('unclick.key'); } catch { /* ignore */ } toast('Stored API key was rejected: used the demo key'); }
      const ok = j.status >= 200 && j.status < 300;
      $('tryMeta').innerHTML = `<span class="chip ${ok ? 'bg-emerald-500 text-ink-950' : 'bg-red-500 text-white'} font-bold">${esc(j.status || 'ERR')}</span><span class="kbd text-[15px] font-bold text-white">${esc(fmtMs(j.ms))}</span>`;
      const body = j.json !== undefined ? j.json : j.error || j.detail;
      const txt = typeof body === 'string' ? body : JSON.stringify(body, null, 2);
      const out = $('tryOut');
      out.className = 'term text-[12.5px] p-3 anim-in';
      try { out.innerHTML = hljs.highlight(txt, { language: 'json' }).value; } catch { out.textContent = txt; }
    } catch (err) {
      $('tryOut').textContent = 'Request failed: ' + err.message;
    } finally { btn.disabled = false; }
  }

  async function wakeTest() {
    const btn = $('wakeBtn');
    btn.disabled = true;
    $('tryMeta').innerHTML = '<span class="chip bg-sky-500/15 text-sky-300 border-sky-500/30"><span class="blink">●</span> waking…</span>';
    try {
      const r = await fetch('/api/wake', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ run_id: S.runId, api_url: S.publish.api_url }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.statusText);
      if (j.local) $('tryMeta').innerHTML = `<span class="text-[12px] text-slate-400" title="Local sandbox mode: the API never pauses">local · /health in</span><span class="kbd text-[20px] font-extrabold text-sky-300">${esc(fmtMs(j.wake_ms))}</span>`;
      else showWake(j.wake_ms);
    } catch (err) {
      $('tryMeta').innerHTML = `<span class="chip bg-red-500/15 text-red-300 border-red-500/30">wake failed</span>`;
      toast('Wake test failed: ' + err.message);
    } finally { btn.disabled = false; }
  }

  let pollT = null;
  function startStatusPoll() {
    clearInterval(pollT);
    if (S.fake) return;
    const id = S.runId;
    pollT = setInterval(() => {
      if (!S || S.runId !== id) { clearInterval(pollT); return; }
      fetch(`/api/runs/${id}/api_status`).catch(() => {});
    }, 8000);
  }

  function boughtKey() {
    try { return localStorage.getItem('unclick.key') || null; } catch { return null; }
  }

  function showWake(ms) {
    const m = $('tryMeta');
    if (m) m.innerHTML = `<span class="text-[12px] text-slate-400">paused → awake in</span><span class="kbd text-[20px] font-extrabold text-sky-300">${esc(fmtMs(ms))}</span>`;
  }

  function onBilling(d, ev) {
    const B = S.billing;
    if (d.calls_remaining != null) {
      B.calls = d.calls_remaining;
      const el = $('callsLeft');
      el.textContent = B.calls;
      el.classList.remove('anim-in'); void el.offsetWidth; el.classList.add('anim-in');
    }
    if (d.api_key) B.apiKey = d.api_key;
    if (d.event === 'paid' && d.api_key) { try { localStorage.setItem('unclick.key', d.api_key); } catch { /* ignore */ } }
    const cents = d.amount_cents != null ? `$${(d.amount_cents / 100).toFixed(2)}` : '';
    if (d.event === 'call_charged' && d.metered === false) {
      B.last = `<div class="flex items-center gap-2"><span class="chip bg-amber-500/15 text-amber-300 border-amber-500/40">demo key</span><span class="text-white font-semibold">1 demo call used</span><div class="flex-1"></div><span class="kbd text-slate-400 text-[11px]">not metered</span><span class="kbd text-slate-500 text-[11px]">${clock(ev.ts)}</span></div>`;
    } else if (d.event === 'call_charged') {
      B.last = `<div class="flex items-center gap-2"><span class="chip bg-stripe/20 text-stripe-light border-stripe/40">usage</span><span class="text-white font-semibold">1 API call charged</span><div class="flex-1"></div><span class="kbd text-white">${esc(cents)}</span><span class="kbd text-slate-500 text-[11px]">${clock(ev.ts)}</span></div>`;
    } else if (d.event === 'paid') {
      B.last = `<div class="flex items-center gap-2"><span class="chip bg-emerald-500/20 text-emerald-300 border-emerald-500/40">paid</span><span class="text-white font-semibold">Checkout complete</span><div class="flex-1"></div><span class="kbd text-white">${esc(cents)}</span><span class="kbd text-slate-500 text-[11px]">${clock(ev.ts)}</span></div>
        ${d.api_key ? `<div class="kbd text-[11.5px] text-slate-400 mt-1">API key ${esc(String(d.api_key).slice(0, 10))}…</div>` : ''}`;
    } else if (d.event === 'checkout_created') {
      B.last = `<div class="flex items-center gap-2"><span class="chip bg-ink-700 text-slate-300">checkout</span><span class="text-slate-300">Checkout session created</span><div class="flex-1"></div><span class="kbd text-slate-500 text-[11px]">${clock(ev.ts)}</span></div>`;
    }
    if (B.last) {
      const lc = $('lastCharge');
      lc.innerHTML = B.last;
      lc.className = 'anim-in rounded-xl bg-ink-800/70 border border-stripe/30 px-3 py-2 text-[13px]';
    }
    B.feed.unshift(`<div class="flex gap-2 anim-in"><span class="kbd text-slate-600">${clock(ev.ts)}</span><span class="truncate">${esc(ev.message)}</span></div>`);
    B.feed = B.feed.slice(0, 4);
    $('billingFeed').innerHTML = B.feed.join('');
  }

  async function buy() {
    const btn = $('buyBtn');
    if (stripeOff()) { toast('Stripe test mode is not configured: demo key active'); return; }
    btn.disabled = true;
    try {
      const fakeBuy = (S && S.fake) || !config.billing;
      const r = await fetch(fakeBuy ? '/api/fake_checkout' : '/api/billing/checkout', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ run_id: S && S.runId, api_id: (S && S.publish && S.publish.sandbox_id) || apiIdParam || '', key: boughtKey() || undefined }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || r.statusText);
      const url = j.checkout_url || j.url;
      if (url) { location.href = url; return; }
      toast(j.fake ? 'Test purchase simulated: +100 calls' : 'Checkout created');
    } catch (err) {
      toast('Checkout failed: ' + err.message);
    } finally { renderBillingMode(); if (!stripeOff()) btn.disabled = false; }
  }

  // ---------------------------------------------------------------------------
  // Timeline + done
  // ---------------------------------------------------------------------------

  const KIND_COLOR = {
    status: 'brand', sandbox: 'brand', live_view: 'sky', egress: 'amber', browser_action: 'sky', capture: 'sky',
    endpoint: 'brand', code: 'violet', run: 'violet', verify: 'violet', publish: 'brand', billing: 'indigo',
    pause: 'sky', log: 'slate', error: 'red', done: 'brand',
  };

  function addLog(ev) {
    const t = S.t0 != null ? ev.ts - S.t0 : 0;
    const c = KIND_COLOR[ev.kind] || 'slate';
    const warn = ev.kind === 'error' || (ev.kind === 'log' && ev.data && ev.data.level === 'warn');
    const el = document.createElement('div');
    el.className = 'flex items-start gap-2 px-2 py-1 rounded-md text-[12.5px] hover:bg-ink-800/60';
    el.innerHTML = `<span class="kbd text-slate-600 tabular-nums w-12 shrink-0 text-right">+${t.toFixed(1)}s</span>
      <span class="chip bg-${c}-500/10 text-${c}-300 border-${c}-500/25 kbd w-[104px] justify-center shrink-0">${esc(ev.kind)}</span>
      <span class="${warn ? 'text-amber-200' : 'text-slate-300'} min-w-0 break-words">${esc(ev.message)}</span>`;
    const pane = $('pane-log');
    const atBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 40;
    pane.appendChild(el);
    if (atBottom) pane.scrollTop = pane.scrollHeight;
  }

  function onDone(d, ev) {
    S.done = true;
    S.tEnd = ev.ts;  // stays right when a finished run is replayed
    $('runDot').className = 'w-2 h-2 rounded-full ' + (S.failed ? 'bg-red-500' : 'bg-brand-400');
    renderStepper();
    $('goBtn').disabled = false;
    if (!S.failed) toast('✓ ' + (d.summary ? `Done: ${d.summary}` : 'Done'), 4000);
  }

  // ---------------------------------------------------------------------------
  // UI plumbing
  // ---------------------------------------------------------------------------

  function showRTab(name) {
    document.querySelectorAll('[data-rtab]').forEach((b) => b.classList.toggle('on', b.dataset.rtab === name));
    document.querySelectorAll('.rpane').forEach((p) => {
      const on = p.id === 'pane-' + name;
      p.classList.toggle('hidden', !on);
      p.classList.toggle('flex', on && name !== 'log');
    });
    if (name === 'log') { const pl = $('pane-log'); pl.scrollTop = pl.scrollHeight; }
  }

  function setFollow(on) {
    follow = on;
    $('followBtn').className = 'chip mr-1 ' + (on ? 'bg-brand-500/10 border-brand-500/30 text-brand-300' : 'bg-ink-800 border-ink-600 text-slate-500');
  }

  const LOOP = [
    ['✍️', 'Write', 'client.py from the captured calls'],
    ['▶', 'Run', 'in a CreateOS sandbox'],
    ['🔎', 'Read', 'the traceback, diff vs the screen'],
    ['🔧', 'Fix', 'and rerun until it matches'],
  ];
  const CODE_EMPTY = `<div class="h-full grid place-items-center font-sans"><div class="text-center">
    <div class="text-[12px] uppercase tracking-[.14em] text-slate-500 font-bold mb-4">The agent's code loop</div>
    <div class="flex items-center gap-2">${LOOP.map(([i, t, d], k) => `
      <div class="w-[150px] rounded-xl border border-ink-700 bg-ink-800/50 px-3 py-3">
        <div class="text-xl">${i}</div><div class="text-white font-semibold mt-1">${t}</div><div class="text-[12px] text-slate-500 mt-0.5 leading-snug">${d}</div>
      </div>${k < LOOP.length - 1 ? '<div class="text-slate-600">→</div>' : ''}`).join('')}
    </div>
    <div class="text-[12.5px] text-slate-600 mt-4">client.py appears here, with a diff for every fix</div>
  </div></div>`;

  function resetUI() {
    $('ticker').innerHTML = '';
    $('liveFrame').src = 'about:blank';
    $('liveDot').classList.add('hidden');
    $('popOut').classList.add('hidden');
    $('deskLabel').textContent = 'DESKTOP';
    $('shotImg').removeAttribute('src');
    $('capList').innerHTML = '<div class="text-slate-600 text-sm" id="capEmpty">Waiting for network traffic…</div>';
    $('capCount').textContent = '0';
    $('endpointBox').classList.add('hidden');
    $('netPanel').classList.remove('hidden');
    $('apiCard').classList.add('hidden');
    $('apiCard').innerHTML = '';
    $('egressHosts').innerHTML = '<div class="text-slate-600 text-sm">Not set yet</div>';
    $('egressReason').innerHTML = '';
    $('sbList').innerHTML = '<div class="text-slate-600 text-sm">No sandboxes yet</div>';
    $('sbCount').textContent = '0';
    $('forkBox').classList.add('hidden');
    $('pauseBox').classList.add('hidden');
    $('codeView').innerHTML = CODE_EMPTY;
    $('attemptPills').innerHTML = '';
    $('codeMeta').innerHTML = '';
    ['codeCount', 'runCount', 'verifyCount'].forEach((id) => { $(id).textContent = '0'; });
    $('pane-runs').innerHTML = '<div class="text-slate-600 text-sm">Sandbox runs appear here with stdout, stderr and exit codes</div>';
    $('pane-verify').innerHTML = '<div class="text-slate-600 text-sm">Output is checked against what Chrome showed</div>';
    $('pane-log').innerHTML = '';
    $('callsLeft').textContent = '—';
    $('lastCharge').className = 'rounded-xl bg-ink-800/70 border border-ink-700 px-3 py-2 text-[13px] text-slate-500';
    $('lastCharge').textContent = 'No charges yet';
    $('billingFeed').innerHTML = '';
    $('timer').textContent = '00:00';
    renderModeBadge();
    renderRoles();
    renderBillingMode();
    showRTab('log');
    setFollow(true);
    renderDesk();
    renderStepper();
    setFocus();
  }

  let toastT;
  function toast(msg, ms = 2600) {
    const t = $('toast');
    t.textContent = msg;
    t.classList.remove('hidden');
    clearTimeout(toastT);
    toastT = setTimeout(() => t.classList.add('hidden'), ms);
  }

  async function copy(text, btn) {
    try { await navigator.clipboard.writeText(text); } catch {
      const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
    }
    const old = btn.textContent; btn.textContent = 'Copied ✓';
    setTimeout(() => { btn.textContent = old; }, 1200);
  }

  async function startReplay(name, speed) {
    try {
      const r = await fetch('/api/runs', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ replay: name, speed: speed ? Number(speed) : undefined }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.statusText);
      attach(j.run_id, j.fake, j);
    } catch (err) { toast('Replay failed: ' + err.message, 5000); }
  }

  // Wire up
  $('runForm').addEventListener('submit', startRun);
  $('buyBtn').addEventListener('click', buy);
  $('followBtn').addEventListener('click', () => setFollow(!follow));
  document.querySelectorAll('[data-rtab]').forEach((b) => b.addEventListener('click', () => { setFollow(false); showRTab(b.dataset.rtab); }));
  $('attemptPills').addEventListener('click', (e) => {
    const b = e.target.closest('[data-attempt]');
    if (!b) return;
    S.selAttempt = Number(b.dataset.attempt);
    renderPills();
    renderCode(false);
  });
  document.querySelectorAll('.dv').forEach((b) => b.addEventListener('click', () => {
    deskView = b.dataset.view;
    try { localStorage.setItem('unclick.deskView', deskView); } catch { /* ignore */ }
    renderDesk();
  }));

  S = fresh();
  resetUI();
  const qs = new URLSearchParams(location.search);
  fetch('/api/config').then((r) => r.json()).then((c) => {
    config = c;
    renderModeBadge();
    renderRoles();
    renderBillingMode();
  }).catch(() => {});
  const apiIdParam = qs.get('api_id') || qs.get('buy') || '';
  const keyM = location.hash.match(/unclick_key=([^&]+)/);
  if (keyM) { try { localStorage.setItem('unclick.key', decodeURIComponent(keyM[1])); } catch { /* ignore */ } }
  const m = location.hash.match(/run=([\w-]+)/);
  let last = null;
  try { last = localStorage.getItem('unclick.lastRun'); } catch { /* ignore */ }
  if (qs.has('replay')) startReplay(qs.get('replay'), qs.get('speed'));
  else if (m) attach(m[1]);
  else if ((qs.has('paid') || qs.has('canceled')) && last) attach(last);
  if (qs.has('paid')) setTimeout(() => toast('✓ Stripe test payment complete: API key issued', 4500), 600);
  if (qs.has('canceled')) setTimeout(() => toast('Checkout canceled', 3000), 600);
  if (qs.has('buy')) setTimeout(buy, 400);
})();

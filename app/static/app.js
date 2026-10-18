/* Unclick mission control. Vanilla JS, renders the EventSink stream (see unclick/contracts.py). */
(() => {
  'use strict';

  const PHASES = [
    ['boot', 'Start', 'Starting a cloud browser'],
    ['explore', 'Watch', 'Watching the page'],
    ['discover', 'Find', 'Finding the data source'],
    ['code', 'Code', 'Writing the code'],
    ['verify', 'Check', 'Checking it matches the screen'],
    ['publish', 'Publish', 'Publishing your API'],
    ['live', 'Live', 'Your API is live'],
  ];
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtMs = (ms) => (ms == null ? '' : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);
  const fmtSize = (b) => (b == null ? '' : b >= 1024 ? `${(b / 1024).toFixed(1)} KB` : `${b} B`);
  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
  const show = (el, on) => { if (typeof el === 'string') el = $(el); if (el) el.hidden = !on; };

  const svg = (d, cls = 'ico', extra = '') => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ${extra}>${d}</svg>`;
  const I = {
    check: '<path d="M20 6 9 17l-5-5"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    ban: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
    desktop: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    term: '<path d="m5 7 5 5-5 5M12 19h7"/>',
    play: '<path d="M7 4v16l13-8z" fill="currentColor" stroke="none"/>',
    moon: '<path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z"/>',
    bolt: '<path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z" fill="currentColor" stroke="none"/>',
    copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
    ext: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    spin: '<path d="M21 12a9 9 0 1 1-6.2-8.6"/>',
    net: '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
    code: '<path d="m8 6-6 6 6 6M16 6l6 6-6 6"/>',
    target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/>',
  };

  let S; // run state
  let es = null;
  let timerInt = null;
  let follow = true;
  let deskView = 'live';
  try { deskView = localStorage.getItem('unclick.deskView') || 'live'; } catch { /* ignore */ }
  let config = { fake: false };
  let billingCfg = null;

  function fresh() {
    return {
      runId: null, lastSeq: -1, t0: null, tEnd: null, phase: null, statusMsg: '', failed: false, done: false,
      sandboxes: new Map(), forks: [], pause: null,
      egress: null, captures: [], endpoint: null,
      codes: [], runs: [], verifies: [], selAttempt: null,
      publish: null, billing: { calls: null, last: null, feed: [], apiKey: null },
      liveUrl: null, shot: null, fake: undefined, deskId: null,
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
    setBusy(true);
    try {
      const r = await fetch('/api/runs', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ url, task }) });
      if (!r.ok) throw new Error(await r.text());
      const j = await r.json();
      attach(j.run_id, j.fake, j);
      revealStage();
    } catch (err) {
      toast('Could not start run: ' + err.message);
      setBusy(false);
    }
  }

  // In the stacked (narrow) layout the stage is below the fold: bring it into view.
  function revealStage() {
    if (window.matchMedia('(max-width: 1099px), (max-height: 680px)').matches) {
      $('stepper').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function setBusy(on) {
    const b = $('goBtn');
    b.disabled = on;
    b.title = on ? 'A run is in progress' : '';
    b.classList.toggle('busy', on);
    $('goLabel').textContent = on ? 'Unclicking…' : 'Unclick it';
  }

  function attach(runId, fake, info) {
    if (es) es.close();
    S = fresh();
    S.runId = runId;
    S.fake = fake;
    if (info && info.mode) setRunMode(info);
    else fetch(`/api/runs/${runId}`).then((r) => r.json()).then((j) => { S.fake = j.fake; setRunMode(j); }).catch(() => {});
    history.replaceState(null, '', location.pathname + location.search + '#run=' + runId);
    setState('running');
    try { localStorage.setItem('unclick.lastRun', runId); } catch { /* ignore */ }
    resetUI();
    $('runId').textContent = runId;
    setBusy(true);
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
        setBusy(false);
        if (r) { history.replaceState(null, '', location.pathname); toast('That run is gone (server restarted)'); }
      }
    };
    clearInterval(timerInt);
    timerInt = setInterval(tick, 250);
  }

  // Never let a local, scripted or replayed run pass as a live CreateOS run: the mode badge says what is running.
  function setRunMode(info) {
    if (!S) return;
    S.mode = info.mode;
    S.replayOf = info.replay_of;
    S.recordedMode = info.recorded_mode;
    if (info.url && info.mode === 'replay') { $('urlIn').value = info.url; $('taskIn').value = info.task || $('taskIn').value; }
    renderModeBadge();
    renderRoles();
    renderBillingMode();
  }

  const MODE = {
    createos: ['LIVE', 'live', 'Real CreateOS sandboxes, real agent'],
    local: ['LOCAL SANDBOX', 'local', 'Local sandbox mode: real agent + local Chrome + local processes. No CreateOS sandboxes; egress, fork and auto-pause are not enforced.'],
    fake: ['SCRIPTED', 'scripted', 'Scripted demo pipeline: no LLM, no sandboxes'],
  };
  function renderModeBadge() {
    const b = $('modeBadge');
    const mode = (S && S.mode) || (config.fake ? 'fake' : config.sandbox) || 'createos';
    let [text, cls, tip] = MODE[mode] || [String(mode).toUpperCase(), 'scripted', ''];
    let sub = '';
    if (mode === 'replay') {
      const rec = S && S.recordedMode;
      [text, cls] = ['REPLAY', 'replay'];
      sub = rec ? ({ createos: 'of a live run', local: 'of a local run', fake: 'of a scripted run' }[rec] || `of ${rec}`) : '';
      tip = `Replaying the recording ${S && S.replayOf ? S.replayOf : ''} (not a live run)`;
    } else if (mode === 'createos' && config.pipeline === false && !config.fake) {
      [text, cls, sub, tip] = ['SCRIPTED', 'scripted', 'no pipeline', 'unclick.pipeline is not importable: the scripted fake pipeline runs instead'];
    }
    b.className = 'mode-badge ' + cls;
    b.title = tip;
    b.innerHTML = `<span class="dot"></span>${esc(text)}${sub ? `<span class="sub">${esc(sub)}</span>` : ''}`;
    b.hidden = false;
  }

  function stripeOn() { return !!(config.stripe || (billingCfg && billingCfg.enabled)); }
  function stripeOff() { return !stripeOn() && !config.fake && !(S && S.fake); }
  function renderBillingMode() {
    const off = stripeOff();
    const btn = $('buyBtn');
    btn.disabled = off;
    btn.title = off ? 'Stripe test mode is not configured (no STRIPE_SECRET_KEY). The published API runs on its demo key.' : 'Opens Stripe Checkout (test mode)';
    const chip = $('stripeModeChip');
    chip.className = 'chip ' + (off ? 'warn' : 'stripe');
    chip.innerHTML = off ? 'Demo key' : '<span class="dot"></span>Test mode';
    chip.title = off ? 'Stripe test mode is not configured: the API accepts its demo key, unmetered' : 'Stripe test mode';
    const calls = (billingCfg && billingCfg.calls) || 100;
    const cents = billingCfg && billingCfg.amount_cents != null ? billingCfg.amount_cents : 100;
    $('buyLabel').textContent = `Buy ${calls} calls`;
    if ($('priceChip')) $('priceChip').textContent = priceText();
    renderChargeNote();
    $('priceBox').innerHTML = off ? '<b>Demo key</b>unmetered' : `<b>$${(cents / 100).toFixed(2)}</b>per ${calls} calls`;
    if (S && !S.billing.last) {
      const lc = $('lastCharge');
      lc.className = 'charge';
      lc.textContent = off ? 'Stripe not configured: demo key active' : 'No charges yet';
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
    if (S.t0 == null) { S.t0 = ev.ts; $('runDot').className = 'run-dot running'; }
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
      case 'publish': S.publishTs = ev.ts; onPublish(d, ev); break;
      case 'health': onHealth(d, ev); break;
      case 'billing': onBilling(d, ev); break;
      case 'pause': onPause(d, ev); break;
      case 'error': toast(ev.message, 5000, 'bad'); break;
      case 'done': onDone(d, ev); break;
      default: break;
    }
  }

  // ---------------------------------------------------------------------------
  // Stepper + focus
  // ---------------------------------------------------------------------------

  function onStatus(d, ev) {
    if (ev.message) $('activityPhase').textContent = ev.message;
    if (d.phase === 'failed') { S.failed = true; S.statusMsg = ev.message; renderStepper(); return; }
    if (!PHASES.some(([k]) => k === d.phase)) return;
    S.phase = d.phase;
    S.statusMsg = ev.message;
    renderStepper();
    setFocus();
  }

  function setFocus() {
    const p = S && S.phase;
    const cls = ['code', 'verify'].includes(p) ? 'focus-code' : ['publish', 'live'].includes(p) && S.publish ? 'focus-live' : 'focus-explore';
    document.body.classList.remove('focus-code', 'focus-live', 'focus-explore');
    document.body.classList.add(cls);
  }

  // Built once, then updated in place so the connector lines can fill smoothly (CSS transitions).
  function buildStepper() {
    const parts = [];
    PHASES.forEach(([k, label, cap], i) => {
      parts.push(`<div class="step" data-step="${k}"><span class="num"><span>${i + 1}</span>${svg(I.check, 'ico-sm ok-i', 'stroke-width="3"')}${svg(I.x, 'ico-sm x-i', 'stroke-width="3"')}</span>
        <span class="txt"><span class="lbl">${label}<span class="attempt-tag" hidden></span></span><span class="cap">${esc(cap)}</span></span></div>`);
      if (i < PHASES.length - 1) parts.push('<div class="step-line"></div>');
    });
    $('stepper').innerHTML = parts.join('');
  }

  function renderStepper() {
    if (!$('stepper').children.length) buildStepper();
    const cur = S && S.phase ? PHASES.findIndex(([k]) => k === S.phase) : -1;
    const attempts = S ? S.codes.length : 0;
    const allDone = S && S.done && !S.failed;
    const steps = $('stepper').querySelectorAll('.step');
    const lines = $('stepper').querySelectorAll('.step-line');
    PHASES.forEach(([k, , cap], i) => {
      const el = steps[i];
      el.classList.toggle('done', !!(allDone || i < cur));
      el.classList.toggle('active', !allDone && i === cur && !S.failed);
      el.classList.toggle('failed', !allDone && i === cur && !!S.failed);
      const caption = cap;
      const c = el.querySelector('.cap');
      if (c.textContent !== caption) { c.textContent = caption; c.title = caption; }
      const tag = el.querySelector('.attempt-tag');
      tag.hidden = !(k === 'code' && attempts > 1);
      tag.textContent = `×${attempts}`;
      if (i === Math.max(cur, 0)) {
        const n = allDone ? PHASES.length : cur + 1;
        $('stepPill').innerHTML = cur < 0 ? 'Starting…' : `<span class="n">Step ${n}/${PHASES.length}</span> · ${esc(allDone ? 'Your API is live' : cap)}`;
      }
      if (lines[i]) {
        lines[i].classList.toggle('done', !!(allDone || i < cur));
        lines[i].classList.toggle('active', !allDone && i === cur && !S.failed);
      }
    });
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
    show(pop, true);
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
    const deskGone = S && S.deskId && (S.sandboxes.get(S.deskId) || {}).action === 'destroyed';
    let view = deskView === 'live' && haveLive ? 'live' : haveShot ? 'shot' : haveLive ? 'live' : null;
    if (deskGone && haveShot) view = 'shot';
    show('liveFrame', view === 'live');
    show('shotImg', view === 'shot');
    show('deskEmpty', !view);
    show('liveDot', view === 'live');
    $('deskLabel').textContent = deskGone ? 'Last frame' : view === 'live' ? 'Live desktop' : view === 'shot' ? 'Desktop' : isLocal() ? 'Local Chrome' : 'Desktop';
    document.querySelectorAll('.dv').forEach((b) => b.classList.toggle('on', b.dataset.view === (view || deskView)));
  }

  // "Unclick working…" feed: newest row on top with a spinner; older rows settle into their final status.
  const VERB = { open: ['Open', 'Opening…', 'Opened'], click: ['Click', 'Clicking…', 'Clicked'], type: ['Type', 'Typing…', 'Typed'],
    press: ['Press', 'Pressing…', 'Pressed'], scroll: ['Scroll', 'Scrolling…', 'Scrolled'], wait: ['Wait', 'Waiting…', 'Done'] };
  function activity(verb, detail, nowStatus, doneStatus, final) {
    const box = $('activity');
    const rows = $('ticker');
    const prev = rows.firstElementChild;
    if (prev && prev.classList.contains('now')) settle(prev);
    const el = document.createElement('div');
    el.className = 'arow ' + (final ? final : 'now');
    el.dataset.done = doneStatus || 'Done';
    const icon = final === 'ok' ? svg(I.check, 'ico-sm', 'stroke-width="2.6"') : final === 'bad' ? svg(I.x, 'ico-sm', 'stroke-width="2.6"') : svg(I.spin, 'ico-sm', 'stroke-width="2.4"');
    el.innerHTML = `<span class="ai">${icon}</span><span class="av">${esc(verb)}</span><span class="ad">${esc(detail)}</span><span class="as">${esc(nowStatus)}</span>`;
    rows.prepend(el);
    while (rows.children.length > 3) rows.lastElementChild.remove();
    box.hidden = false;
  }
  function settle(el) {
    el.classList.remove('now');
    el.classList.add('ok');
    el.querySelector('.ai').innerHTML = svg(I.check, 'ico-sm', 'stroke-width="2.6"');
    el.querySelector('.as').textContent = el.dataset.done;
  }
  function onAction(d, ev) {
    const [verb, now, done] = VERB[d.action] || [d.action || 'Do', 'Working…', 'Done'];
    if (d.action === 'open') $('activityTitle').textContent = 'Watching the page…';
    activity(verb, d.detail || ev.message, now, done);
  }

  // ---------------------------------------------------------------------------
  // Network captures + endpoint
  // ---------------------------------------------------------------------------

  function splitUrl(u) {
    try { const x = new URL(u); return { host: x.host, path: x.pathname + x.search }; } catch { return { host: '', path: u }; }
  }

  function onCapture(d) {
    if (!S.captures.length) $('capList').innerHTML = '';
    S.captures.push(d);
    $('capCount').textContent = S.captures.length;
    const { host, path } = splitUrl(d.url);
    const ok = d.status >= 200 && d.status < 300;
    const el = document.createElement('div');
    el.className = 'cap';
    el.dataset.url = d.url;
    el.title = d.url;
    el.innerHTML = `<span class="method">${esc(d.method || 'GET')}</span>
      <span class="status ${ok ? 'ok' : 'bad'}">${esc(d.status)}</span>
      <span class="u"><b>${esc(host)}</b>${esc(path)}</span>
      <span class="size">${esc(fmtSize(d.size))}</span>`;
    $('capList').appendChild(el);
    $('capList').scrollTop = $('capList').scrollHeight;
    if (S.endpoint) markPicked();
  }

  function onEndpoint(d) {
    { const u = splitUrl(d.url); activity('Found the data source', u.host + u.path, 'Found', '', 'ok'); }
    S.endpoint = d;
    const { host, path } = splitUrl(d.url);
    const box = $('endpointBox');
    box.className = 'endpoint';
    box.hidden = false;
    box.innerHTML = `<div class="endpoint-k">${svg(I.target, 'ico-sm')}Hidden API found</div>
      <div class="endpoint-url"><span class="chip info mono" style="height:20px;margin-right:6px">${esc(d.method || 'GET')}</span><b>${esc(host)}</b>${esc(path)}</div>
      ${d.why ? `<div class="endpoint-why">${esc(d.why)}</div>` : ''}`;
    markPicked();
  }

  function markPicked() {
    let picked = null;
    document.querySelectorAll('.cap').forEach((el) => {
      const hit = el.dataset.url === S.endpoint.url;
      el.classList.toggle('picked', hit);
      el.classList.toggle('dim', !hit);
      if (hit && !picked) picked = el;
    });
    if (picked) picked.scrollIntoView({ block: 'nearest' });
  }

  // ---------------------------------------------------------------------------
  // Egress
  // ---------------------------------------------------------------------------

  function onEgress(d) {
    revealCard('egCard');
    const prev = S.egress ? S.egress.allow : [];
    const allow = d.allow || [];
    const added = d.added || allow.filter((h) => !prev.includes(h));
    const removed = prev.filter((h) => !allow.includes(h));
    S.egress = { allow, added, removed, reason: d.reason };
    const chips = allow.map((h) => {
      const isNew = added.includes(h) && prev.length > 0;
      return `<span class="host ${isNew ? 'new' : ''}">${svg(isNew ? I.plus : I.lock, 'ico-sm')}${esc(h)}</span>`;
    });
    removed.forEach((h) => chips.push(`<span class="host gone" title="removed">${svg(I.ban, 'ico-sm')}${esc(h)}</span>`));
    if (!allow.length) chips.push('<span class="chip bad">Open: no allowlist</span>');
    $('egressHosts').innerHTML = chips.join('');
    $('egressReason').textContent = d.reason || '';
    const mode = $('egressMode');
    mode.textContent = allow.length ? `${allow.length} host${allow.length > 1 ? 's' : ''} allowed` : 'open';
    mode.className = 'chip ' + (allow.length ? 'warn' : 'bad');
  }

  // ---------------------------------------------------------------------------
  // Sandboxes, fork, pause
  // ---------------------------------------------------------------------------

  const ROLE = {
    desktop: { label: 'Desktop', sub: 'desktop:1 · Chrome' },
    runner: { label: 'Runner', sub: 'devbox:1 · runs client.py' },
    api: { label: 'Public API', sub: 'ingress · auto-pause' },
  };
  const ROLE_SUB = { desktop: 'desktop:1 · Chrome', runner: 'devbox:1 · runs client.py', api: 'ingress · auto-pause' };
  const ROLE_SUB_LOCAL = { desktop: 'local process · local Chrome', runner: 'local process · runs client.py', api: 'local process · 127.0.0.1' };
  function isLocal() { return S && (S.mode === 'local' || (S.mode === 'replay' && S.recordedMode === 'local')); }
  function renderRoles() {
    const subs = isLocal() ? ROLE_SUB_LOCAL : ROLE_SUB;
    Object.keys(ROLE).forEach((k) => { ROLE[k].sub = subs[k]; });
    if (S && S.sandboxes.size) renderSandboxes();
    const local = isLocal() || (S && !S.mode && config.sandbox === 'local');
    $('sbKicker').textContent = local ? 'Local mode' : 'CreateOS';
    $('sbTitle').textContent = local ? 'Local sandboxes' : 'Sandboxes';
    $('deskEmptyTitle').textContent = local ? 'Local Chrome (local sandbox mode)' : 'CreateOS desktop sandbox';
    renderDesk();
  }
  const STATE = {
    created: ['Running', 'ok'], forked: ['Running', 'ok'], resumed: ['Running', 'ok'],
    pausing: ['Pausing', 'warn pulse'], paused: ['Paused', 'warn'], destroyed: ['Destroyed', ''],
  };

  function onSandbox(d) {
    revealCard('sbCard');
    const prev = S.sandboxes.get(d.id) || {};
    const sb = { ...prev, id: d.id, role: d.role || prev.role, action: d.action };
    if (d.ms != null && ['created', 'forked'].includes(d.action)) sb.bootMs = d.ms;
    if (d.forked_from) sb.forkedFrom = d.forked_from;
    S.sandboxes.set(d.id, sb);
    if (sb.role === 'desktop') {
      S.deskId = d.id;
      const chip = $('deskId');
      chip.textContent = d.id;
      show(chip, true);
      renderDesk();
    }
    if (d.action === 'forked' && d.forked_from) S.forks.push({ from: d.forked_from, to: d.id, ms: d.ms });
    renderSandboxes();
  }

  function renderSandboxes() {
    const list = [...S.sandboxes.values()];
    const live = list.filter((s) => s.action !== 'destroyed').length;
    const sc = $('sbCount');
    sc.textContent = `${live} active`;
    sc.className = 'chip' + (live ? ' ok' : '');
    // Live sandboxes first, destroyed ones collapse to one quiet line at the bottom.
    const order = [...list.filter((s) => s.action !== 'destroyed'), ...list.filter((s) => s.action === 'destroyed')];
    $('sbList').innerHTML = order.map((s) => {
      const r = ROLE[s.role] || { label: s.role || 'sandbox', sub: '' };
      const [st, c] = STATE[s.action] || [s.action, ''];
      if (s.action === 'destroyed') {
        return `<div class="sbrow dead">${svg(I.x, 'ico-sm')}<div class="sbname grow">${esc(r.label)} <span class="id">${esc(s.id)}</span></div><span class="faint" style="font-size:12px">destroyed · slot freed</span></div>`;
      }
      const meta = s.bootMs ? ` · ${s.forkedFrom ? 'forked' : 'up'} in ${fmtMs(s.bootMs)}` : '';
      return `<div class="sbrow">
        <div class="sbicon ${esc(s.role)}">${svg(s.role === 'desktop' ? I.desktop : s.role === 'api' ? I.globe : I.term)}</div>
        <div class="grow"><div class="sbname">${esc(r.label)} <span class="id">${esc(s.id)}</span></div><div class="sbsub">${esc(r.sub)}${esc(meta)}</div></div>
        <span class="chip ${c}"><span class="dot"></span>${esc(st)}</span>
      </div>`;
    }).join('') || '<div class="empty">No sandboxes yet</div>';

    const fb = $('forkBox');
    if (S.forks.length) {
      const f = S.forks[S.forks.length - 1];
      const from = S.sandboxes.get(f.from) || { role: 'runner' };
      const to = S.sandboxes.get(f.to) || { role: 'api' };
      fb.hidden = false;
      fb.className = 'fork';
      fb.innerHTML = `<div class="fork-row"><span class="fork-k">Fork</span><span>${esc((ROLE[from.role] || {}).label || from.role)}</span>
          <svg viewBox="0 0 100 14" preserveAspectRatio="none"><path class="fork-path" d="M2 7 H92" stroke-width="2" fill="none"/><path d="M88 2 L98 7 L88 12" stroke="rgb(var(--accent))" stroke-width="2" fill="none"/></svg>
          <span>${esc((ROLE[to.role] || {}).label || to.role)}</span>${f.ms ? `<b class="mono" style="margin-left:4px">${esc(fmtMs(f.ms))}</b>` : ''}</div>
        <div class="fork-sub">Same warm memory + disk, tighter egress</div>`;
    }
  }

  function onPause(d) {
    S.pause = { state: d.state, wake_ms: d.wake_ms ?? (S.pause && d.state !== 'paused' ? S.pause.wake_ms : null) };
    renderPause();
    if (d.state === 'awake' && d.wake_ms != null) showWake(d.wake_ms);
  }

  function renderPause() {
    const p = S.pause;
    const box = $('pauseBox');
    if (!p) { box.hidden = true; return; }
    box.hidden = false;
    const cfg = {
      paused: [svg(I.moon), 'API paused', 'Idle → auto-paused. No vCPU billing until the next request.'],
      waking: [svg(I.spin, 'ico', 'style="animation:spin .8s linear infinite"'), 'Waking…', 'Request arrived: resuming on demand'],
      awake: [svg(I.bolt), 'API awake', 'Woke on request'],
    }[p.state] || ['', p.state, ''];
    const [icon, label, sub] = cfg;
    box.className = 'pauserow ' + esc(p.state);
    box.innerHTML = `${icon}<div class="grow"><div class="pt">${esc(label)}</div><div class="ps">${esc(sub)}</div></div>
      ${p.state === 'awake' && p.wake_ms != null ? `<div class="pv"><b>${esc(fmtMs(p.wake_ms))}</b><span>wake</span></div>` : ''}`;
    const sc = box.parentElement;
    sc.scrollTop = sc.scrollHeight;
  }

  // ---------------------------------------------------------------------------
  // Code / runs / verify
  // ---------------------------------------------------------------------------

  function onCode(d) {
    activity('Writing code', `${d.filename || 'client.py'} · attempt ${d.attempt}`, 'Writing…', 'Written');
    revealAgent();
    S.codes = S.codes.filter((c) => c.attempt !== d.attempt);
    S.codes.push(d);
    S.codes.sort((a, b) => a.attempt - b.attempt);
    S.selAttempt = d.attempt;
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
      const mark = st === 'ok' ? '✓' : st === 'fail' ? '✕' : st === 'ran' ? '▶' : '…';
      return `<button type="button" data-attempt="${c.attempt}" class="pill ${st} ${sel ? 'on' : ''}"><span class="m">${mark}</span>Attempt ${c.attempt}</button>`;
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
    const diffTxt = prev ? ` · <span class="add">+${added}</span> <span class="del">−${removed}</span> vs #${prev.attempt}` : '';
    $('codeMeta').innerHTML = `<span class="mono" style="color:rgb(var(--text-2))">${esc(cur.filename || 'client.py')}</span> · ${curCode.split('\n').length} lines${diffTxt}`;
    if (firstChange >= 0) {
      const el = view.children[firstChange];
      if (el) view.scrollTop = Math.max(0, el.offsetTop - view.clientHeight * 0.3);
    } else view.scrollTop = 0;
  }

  function line(kind, n, html, delayIdx) {
    const gut = kind === 'add' ? '+' : kind === 'del' ? '−' : '';
    const anim = delayIdx >= 0 ? ` new" style="animation-delay:${Math.min(delayIdx * 22, 1400)}ms` : '';
    return `<div class="code-line ${kind}${anim}"><span class="ln">${n}</span><span class="gut">${gut}</span><span class="src">${html || ' '}</span></div>`;
  }

  // Let long URLs wrap at / . - ? & instead of mid-word.
  function breakable(u) { return esc(u).replace(/([/.?&-])/g, '$1<wbr>'); }

  function clip(s, max = 60) {
    const lines = String(s || '').split('\n');
    return lines.length > max ? lines.slice(0, max).join('\n') + `\n… (${lines.length - max} more lines)` : String(s || '');
  }

  function clearEmpty(pane, n) { if (n === 1) pane.innerHTML = ''; }

  function onRun(d) {
    activity('Running it', `attempt ${d.attempt}`, d.exit_code === 0 ? 'Ran cleanly' : `Error · exit ${d.exit_code}`, '', d.exit_code === 0 ? 'ok' : 'bad');
    S.runs.push(d);
    $('runCount').textContent = S.runs.length;
    const ok = d.exit_code === 0;
    const el = document.createElement('div');
    el.className = 'box ' + (ok ? 'ok' : 'bad');
    el.innerHTML = `<div class="box-h">
        <span class="cmd">$ python ${esc((S.codes.find((c) => c.attempt === d.attempt) || {}).filename || 'client.py')}</span>
        <span class="faint">attempt ${esc(d.attempt)}</span>
        <span class="grow"></span>
        <span class="mono faint" style="font-size:12px">${esc(fmtMs(d.ms))}</span>
        <span class="chip ${ok ? 'solid-ok' : 'solid-bad'}">exit ${esc(d.exit_code)}</span>
      </div>
      ${d.stderr ? `<pre class="term err">${esc(clip(d.stderr, 40))}</pre>` : ''}
      ${d.stdout ? `<pre class="term">${esc(clip(d.stdout, 60))}</pre>` : ''}
      ${!d.stdout && !d.stderr ? '<div class="term faint">(no output)</div>' : ''}`;
    const pane = $('pane-runs');
    clearEmpty(pane, S.runs.length);
    pane.appendChild(el);
    if (follow) showRTab('runs');
    pane.scrollTop = el.offsetTop - 12;
    renderPills();
  }

  function onVerify(d) {
    activity('Checking it matches the screen', `attempt ${d.attempt}`, d.ok ? 'Matches' : 'Does not match', '', d.ok ? 'ok' : 'bad');
    S.verifies.push(d);
    $('verifyCount').textContent = S.verifies.length;
    const el = document.createElement('div');
    el.className = 'box ' + (d.ok ? 'ok' : 'bad');
    el.innerHTML = `<div class="box-h">
        <span class="chip ${d.ok ? 'solid-ok' : 'solid-bad'}">${d.ok ? '✓ Match' : '✕ Mismatch'}</span>
        <span class="faint">attempt ${esc(d.attempt)}</span>
      </div>
      <div class="vgrid">
        <div><div class="vk">Expected · on screen</div><pre class="term">${esc(clip(d.expected, 14))}</pre></div>
        <div><div class="vk ${d.ok ? 'ok' : 'bad'}">Got · from client.py</div><pre class="term" style="color:rgb(var(${d.ok ? '--ok' : '--bad'}))">${esc(clip(d.got, 14))}</pre></div>
      </div>
      ${d.notes ? `<div class="vnote"><span class="faint">Agent:</span> ${esc(d.notes)}</div>` : ''}`;
    const pane = $('pane-verify');
    clearEmpty(pane, S.verifies.length);
    pane.appendChild(el);
    if (follow) showRTab('verify');
    pane.scrollTop = el.offsetTop - 12;
    renderPills();
  }

  // ---------------------------------------------------------------------------
  // Publish / API card / billing
  // ---------------------------------------------------------------------------

  function apiBase(u) { return String(u || '').replace(/\/v1\/run\/?$/, '').replace(/\/$/, ''); }

  function onPublish(d) {
    // Newer publish events carry openapi_url / mcp_url / mcp_config / fields; derive sensible defaults when missing.
    const base = apiBase(d.api_url);
    const keyM = /X-(?:Unclick|API)-Key:\s*([^'"\s]+)/i.exec(d.curl || '');
    const name = d.tool_name || d.name || 'unclick';
    S.publish = {
      ...d,
      key: keyM ? keyM[1] : '',
      keyHeader: (/(X-(?:Unclick|API)-Key)/i.exec(d.curl || '') || [])[1] || 'X-Unclick-Key',
      openapi_url: d.openapi_url || (base ? base + '/openapi.json' : ''),
      mcp_url: d.mcp_url || (base ? base + '/mcp' : ''),
      mcp_config: d.mcp_config || (base ? JSON.stringify({ mcpServers: { [name]: { url: base + '/mcp', headers: keyM ? { 'X-Unclick-Key': keyM[1] } : {} } } }, null, 2) : ''),
    };
    revealCard('payCard');
    renderApiPage();
    startStatusPoll();
    renderStepper();
    setFocus();
    renderSummary();
    refreshCatalogCount();
    setState('result');
    window.scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }

  // ---------------------------------------------------------------------------
  // API page (result state)
  // ---------------------------------------------------------------------------

  function initialParams() {
    // From the curl example's query string (e.g. ?limit=10); the OpenAPI doc can add more later.
    const out = {};
    const m = /'(https?:\/\/[^']+)'/.exec(S.publish.curl || '') || [null, S.publish.api_url];
    try { new URL(m[1]).searchParams.forEach((v, k) => { out[k] = v; }); } catch { /* ignore */ }
    if (!Object.keys(out).length && S.mode === 'replay') {
      // A replay's API is gone, so no OpenAPI doc: read the params from the recorded client.run() signature.
      const code = (S.codes[S.codes.length - 1] || {}).code || '';
      const sig = /def run\(([\s\S]*?)\)\s*(->[^:]*)?:/.exec(code);
      if (sig) sig[1].split(',').forEach((a) => {
        const p = /^\s*(\w+)\s*(?::\s*[\w\[\], |]+)?\s*=\s*(.+?)\s*$/.exec(a);
        if (p && p[1] !== 'page') out[p[1]] = p[2].replace(/^["']|["']$/g, '').replace(/^None$/, '');
      });
    }
    if (!Object.keys(out).length) out.limit = '10';
    return out;
  }

  function apiUrlWith(params) {
    try {
      const u = new URL(S.publish.api_url);
      Object.entries(params).forEach(([k, v]) => { if (v !== '' && v != null) u.searchParams.set(k, v); else u.searchParams.delete(k); });
      return u.toString();
    } catch { return S.publish.api_url; }
  }

  function currentParams() {
    const out = {};
    document.querySelectorAll('#params input').forEach((i) => { out[i.name] = i.value.trim(); });
    return out;
  }

  function priceText() {
    const calls = (billingCfg && billingCfg.calls) || 100;
    const cents = billingCfg && billingCfg.amount_cents != null ? billingCfg.amount_cents : 500;
    const per = cents / calls / 100;
    return stripeOff() ? 'Demo key · unmetered' : `$${per.toFixed(per < 0.1 ? 2 : 2)} / call · test mode`;
  }

  function apiDisplayName() {
    const P = S.publish;
    if (P.name && P.name !== P.api_id && P.name !== P.sandbox_id) return P.name;
    let host = '';
    try { host = new URL($('runUrl').textContent || $('urlIn').value).host.replace(/^www\./, ''); } catch { /* ignore */ }
    return host ? `${host} API` : 'Your API';
  }

  function renderApiPage() {
    const P = S.publish;
    const page = $('apiPage');
    const src = $('runUrl').textContent || $('urlIn').value;
    const params = initialParams();
    page.innerHTML = `
      <div class="card api-head">
        <div class="api-badges">
          <span class="live-pill"><span class="dot"></span>LIVE</span>
          <span class="method">GET</span>
          <span id="healthChip"><span class="chip" title="Not verified since publishing">Verified at publish</span></span>
          <span class="chip stripe" id="priceChip">${esc(priceText())}</span>
        </div>
        <h2 class="api-name">${esc(apiDisplayName())}</h2>
        <div class="api-src">From <a href="${esc(src)}" target="_blank" rel="noopener">${esc(src.replace(/^https?:\/\//, ''))}</a>${$('runTask').textContent ? ` · ${esc($('runTask').textContent)}` : ''}</div>
        <div class="urlbox"><span class="u">${breakable(P.api_url)}</span><button type="button" class="btn btn-ghost btn-sm" data-copy-text="${esc(P.api_url)}">${svg(I.copy, 'ico-sm')}Copy URL</button></div>
        <div class="api-actions">
          <button type="button" id="apiVerify" class="btn btn-ghost">${svg(I.check, 'ico-sm')}Verify</button>
          <button type="button" id="apiHeal" class="btn btn-ghost">${svg(I.bolt, 'ico-sm')}Self-heal</button>
          <button type="button" id="wakeBtn" class="btn btn-ghost" title="Pause-and-wake round trip">${svg(I.moon, 'ico-sm')}Wake test</button>
          ${P.docs_url ? `<a class="btn btn-ghost" href="${esc(P.docs_url)}" target="_blank" rel="noopener">Docs ${svg(I.ext, 'ico-sm')}</a>` : ''}
          <span class="grow"></span>
          <button type="button" class="btn btn-ink" data-new>Build another</button>
        </div>
      </div>
      <div class="api-grid">
        <div class="card api-card">
          <div class="card-h"><span class="card-title">Try it</span><span class="grow"></span><span class="faint small">Real request, no LLM involved</span></div>
          <div class="card-b">
            <form id="playForm" class="playform" autocomplete="off">
              <div id="params" class="params">${Object.entries(params).map(([k, v]) => `<label class="param"><span>${esc(k)}</span><input name="${esc(k)}" value="${esc(v)}" inputmode="${/^\d+$/.test(v) ? 'numeric' : 'text'}" /></label>`).join('')}</div>
              <div class="send-row" style="margin-top:12px">
                <button type="submit" id="tryBtn" class="btn btn-primary">${svg(I.play, 'ico-sm')}Send request</button>
                <div id="tryMeta" class="resp-meta" aria-live="polite"></div>
              </div>
            </form>
            <div class="resp" id="respBox"><div class="hint">Press <b>Send request</b> to call your API. The response appears here.</div></div>
          </div>
        </div>
        <div class="card api-card">
          <div class="card-h"><span class="card-title">Use it</span><span class="grow"></span>
            <div class="codetabs" role="tablist" id="codeTabs">
              ${['curl', 'Python', 'JavaScript', 'MCP'].map((t, i) => `<button type="button" role="tab" data-ct="${t}" class="${i ? '' : 'on'}">${t}</button>`).join('')}
            </div>
          </div>
          <div class="card-b">
            <div class="codebox"><pre id="codeOut"></pre><button type="button" class="btn btn-ghost btn-sm copy" id="codeCopy">${svg(I.copy, 'ico-sm')}Copy</button></div>
            <div class="code-note" id="codeNote"></div>
          </div>
        </div>
      </div>
      <div class="api-grid">
        <div class="card api-card">
          <div class="card-h"><span class="card-title">Schema</span><span class="grow"></span><span class="faint small" id="schemaSrc"></span></div>
          <div class="card-b"><div class="fields" id="schemaFields"><span class="skel" style="width:120px;height:26px"></span><span class="skel" style="width:90px;height:26px"></span><span class="skel" style="width:110px;height:26px"></span></div></div>
        </div>
        <div class="card api-card speedcard" id="speedCard"></div>
      </div>`;
    page.hidden = false;
    page.querySelector('#playForm').addEventListener('submit', (e) => { e.preventDefault(); sendTry(); });
    page.querySelector('#wakeBtn').onclick = wakeTest;
    page.querySelector('#apiVerify').onclick = () => apiAction('verify');
    page.querySelector('#apiHeal').onclick = () => apiAction('heal');
    page.querySelector('[data-new]').onclick = buildAnother;
    page.querySelectorAll('[data-copy-text]').forEach((b) => { b.onclick = () => copy(b.dataset.copyText, b); });
    page.querySelectorAll('[data-ct]').forEach((b) => { b.onclick = () => showCodeTab(b.dataset.ct); });
    page.querySelector('#params').addEventListener('input', () => showCodeTab(currentCodeTab));
    showCodeTab('curl');
    renderSpeed();
    loadSchema().then(({ fields, src: s2 }) => renderSchema(fields, s2));
    loadOpenApiParams();
  }

  let currentCodeTab = 'curl';
  function showCodeTab(t) {
    currentCodeTab = t;
    const P = S.publish;
    document.querySelectorAll('[data-ct]').forEach((b) => b.classList.toggle('on', b.dataset.ct === t));
    const url = apiUrlWith(currentParams());
    const kh = P.keyHeader;
    const key = P.key || 'YOUR_KEY';
    let code = '', note = '', lang = 'bash';
    if (t === 'curl') { code = `curl -s '${url}' \\\n  -H '${kh}: ${key}'`; note = 'Works from any terminal.'; }
    else if (t === 'Python') { lang = 'python'; code = `import httpx\n\nr = httpx.get(\n    "${url}",\n    headers={"${kh}": "${key}"},\n    timeout=60,\n)\nr.raise_for_status()\nrows = r.json()\nprint(len(rows), "rows")`; note = 'pip install httpx'; }
    else if (t === 'JavaScript') { lang = 'javascript'; code = `const r = await fetch("${url}", {\n  headers: { "${kh}": "${key}" },\n});\nconst rows = await r.json();\nconsole.log(rows.length, "rows");`; note = 'Node 18+ or any browser (server-side keeps your key private).'; }
    else { lang = 'json'; code = prettyJson(P.mcp_config); note = P.mcp_url ? `Paste into Claude Desktop or Cursor's MCP settings. Endpoint: ${P.mcp_url}` : 'This API was published without an MCP endpoint.'; }
    const out = $('codeOut');
    try { out.innerHTML = hljs.highlight(code, { language: lang, ignoreIllegals: true }).value; } catch { out.textContent = code; }
    $('codeNote').textContent = note;
    $('codeCopy').onclick = (e) => copy(code, e.currentTarget);
    $('codeCopy').innerHTML = `${svg(I.copy, 'ico-sm')}${t === 'MCP' ? 'Copy config' : 'Copy'}`;
  }

  function renderSchema(fields, src) {
    const box = $('schemaFields');
    if (!box) return;
    box.innerHTML = fields.length
      ? fields.map((f, i) => `<span class="field-chip" style="animation-delay:${i * 50}ms"><span class="n">${esc(f.name)}${f.required === false ? '?' : ''}</span><span class="t ${esc(String(f.type).split('|')[0])}">${esc(f.type)}</span></span>`).join('')
      : '<span class="faint">Send a request to see the fields.</span>';
    $('schemaSrc').textContent = fields.length ? src : '';
  }

  async function loadOpenApiParams() {
    const P = S.publish;
    if (!P.openapi_url || S.fake) return;
    try {
      const r = await fetch(P.openapi_url);
      if (!r.ok) return;
      const doc = await r.json();
      const op = Object.values(doc.paths || {}).map((o) => o && o.get).find(Boolean);
      const ps = (op && op.parameters || []).filter((x) => x.in === 'query' && !/key/i.test(x.name));
      if (!ps.length) return;
      const cur = currentParams();
      $('params').innerHTML = ps.map((x) => {
        const v = cur[x.name] ?? (x.schema && x.schema.default != null ? x.schema.default : '');
        return `<label class="param" title="${esc(x.description || '')}"><span>${esc(x.name)}${x.required ? '' : '?'}</span><input name="${esc(x.name)}" value="${esc(v)}" /></label>`;
      }).join('');
      showCodeTab(currentCodeTab);
    } catch { /* CORS: keep the params from the curl example */ }
  }

  function rowCount(v) {
    if (Array.isArray(v)) return v.length;
    if (v && typeof v === 'object') { const a = Object.values(v).find(Array.isArray); return a ? a.length : null; }
    return null;
  }

  async function sendTry() {
    const btn = $('tryBtn');
    const box = $('respBox');
    btn.disabled = true;
    btn.title = 'Waiting for the response';
    $('tryMeta').innerHTML = '<span class="chip info pulse"><span class="dot"></span>Calling your API…</span>';
    box.innerHTML = '<div style="padding:14px 16px"><div class="skel skel-row" style="width:40%"></div><div class="skel skel-row"></div><div class="skel skel-row" style="width:75%"></div></div>';
    const url = apiUrlWith(currentParams());
    try {
      const r = await fetch('/api/try', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ run_id: S.runId, api_url: url, key: boughtKey() }) });
      const j = await r.json().catch(() => ({}));
      if (j.key_rejected) { try { localStorage.removeItem('unclick.key'); } catch { /* ignore */ } toast('Your stored key was rejected, so the demo key was used'); }
      const ok = j.status >= 200 && j.status < 300;
      if (!ok) {
        const why = j.status === 402 ? 'Out of calls. Buy more calls to keep using this API.' : j.status === 409 ? 'This is a replay: the API it published is not running any more.' : (j.error || j.detail || (j.json && j.json.error) || `The API answered ${j.status || 'with an error'}.`);
        throw new Error(typeof why === 'string' ? why : JSON.stringify(why));
      }
      S.tryMs = j.ms;
      if (j.json && typeof j.json === 'object') S.lastTry = j.json;
      const n = rowCount(j.json);
      S.pendingCharge = true;
      $('tryMeta').innerHTML = `<span class="chip solid-ok">${esc(j.status)} OK</span><span><b>${esc(fmtMs(j.ms))}</b></span>${n != null ? `<span><b>${n}</b> rows</span>` : ''}<span id="chargeNote"></span>`;
      renderChargeNote();
      const txt = JSON.stringify(j.json, null, 2);
      box.innerHTML = '<pre></pre>';
      try { box.firstChild.innerHTML = hljs.highlight(txt, { language: 'json' }).value; } catch { box.firstChild.textContent = txt; }
      renderSpeed();
      if (!(S.publish.fields || []).length) loadSchema().then(({ fields, src }) => renderSchema(fields, src));
    } catch (err) {
      $('tryMeta').innerHTML = '';
      box.innerHTML = `<div class="err">${svg(I.x, 'ico')}<span>${esc(err.message === 'Failed to fetch' ? 'Could not reach the Unclick server. Check that it is running.' : err.message)}</span><button type="button" class="btn btn-ghost btn-sm" id="retryTry">Retry</button></div>`;
      $('retryTry').onclick = sendTry;
    } finally { btn.disabled = false; btn.title = ''; }
  }

  function renderChargeNote() {
    const el = $('chargeNote');
    if (!el || !S.pendingCharge) return;
    const B = S.billing;
    if (B.calls == null) { el.textContent = ''; return; }
    el.innerHTML = stripeOff() ? `1 demo call used · <b style="font-size:15px">${esc(B.calls)}</b> left` : `charged 1 call · <b style="font-size:15px">${esc(B.calls)}</b> left`;
  }

  async function apiAction(kind) {
    const P = S.publish;
    const id = P.api_id || P.sandbox_id;
    const btn = $(kind === 'verify' ? 'apiVerify' : 'apiHeal');
    btn.disabled = true;
    const old = btn.innerHTML;
    btn.innerHTML = `${svg(I.spin, 'ico-sm', 'style="animation:spin .8s linear infinite"')}${kind === 'verify' ? 'Verifying…' : 'Healing…'}`;
    try {
      const r = await fetch(`/api/apis/${encodeURIComponent(id)}/${kind}`, { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      if (r.status === 404) throw new Error(S.fake ? 'Scripted demo APIs are not saved to My APIs, so there is nothing to verify.' : 'This API is not in My APIs yet.');
      if (!r.ok) throw new Error(j.detail || r.statusText);
      if (kind === 'verify') {
        onHealth({ api_id: id, ok: j.ok, healthy: j.healthy ?? j.ok, last_verified: j.last_verified, problems: j.problems });
        toast(j.ok ? 'Verified: the output still matches the schema' : `Needs attention: ${(j.problems || [])[0] || 'verification failed'}`, 3500, j.ok ? 'ok' : 'bad');
      } else if (j.run_id) {
        toast('Self-heal started: re-running the agent', 3000, 'ok');
        attach(j.run_id, undefined, j.mode ? { mode: j.mode } : undefined);
      } else toast(j.ok ? 'Healthy: nothing to heal' : 'Self-heal did not start', 3000, j.ok ? 'ok' : 'bad');
    } catch (err) { toast(err.message, 4500, 'bad'); }
    finally { if (document.body.contains(btn)) { btn.disabled = false; btn.innerHTML = old; } }
  }

  function showLTab(name) {
    const P = S.publish;
    if (!P) return;
    document.querySelectorAll('[data-ltab]').forEach((b) => b.classList.toggle('on', b.dataset.ltab === name));
    const pane = $('lpane');
    const hint = $('ltabHint');
    if (name === 'curl') {
      hint.textContent = 'Call it from anywhere';
      pane.innerHTML = `<div class="lpane"><span class="p">$</span><code>${esc(P.curl || `curl -s '${P.api_url}'`)}</code><button type="button" class="btn btn-ghost btn-sm" data-c>${svg(I.copy, 'ico-sm')}curl</button></div>`;
      pane.querySelector('[data-c]').onclick = (e) => copy(P.curl || P.api_url, e.currentTarget);
    } else if (name === 'mcp') {
      hint.textContent = 'Use as a tool in Claude / Cursor';
      pane.innerHTML = P.mcp_url ? `<div class="lpane"><div class="lpane-col">
          <div class="mcp-url"><b>MCP</b>${breakable(P.mcp_url)}</div>
          <pre>${esc(prettyJson(P.mcp_config))}</pre></div>
          <button type="button" class="btn btn-ink btn-sm" data-c title="Copy the config for Claude Desktop / Cursor">${svg(I.copy, 'ico-sm')}Use as a tool</button></div>`
        : '<div class="lpane faint">This API was published without an MCP endpoint.</div>';
      const b = pane.querySelector('[data-c]');
      if (b) b.onclick = (e) => copy(P.mcp_config, e.currentTarget);
    } else {
      hint.textContent = 'Typed response fields';
      pane.innerHTML = '<div class="lpane faint">Loading schema…</div>';
      loadSchema().then(({ fields, src }) => {
        if (!S.publish || !document.querySelector('[data-ltab="schema"].on')) return;
        pane.innerHTML = fields.length
          ? `<div class="lpane"><div class="fields">${fields.map((f, i) => `<span class="field-chip" style="animation-delay:${i * 50}ms"><span class="n">${esc(f.name)}${f.required === false ? '?' : ''}</span><span class="t ${esc(String(f.type).split('|')[0])}">${esc(f.type)}</span></span>`).join('')}
              <div class="schema-src">${esc(src)}</div></div></div>`
          : '<div class="lpane faint">No schema available yet: press Try it, then reopen this tab.</div>';
      });
    }
  }

  function prettyJson(t) { try { return JSON.stringify(JSON.parse(t), null, 2); } catch { return String(t || ''); } }

  // Schema fields: publish event > OpenAPI document > inferred from the verified output.
  async function loadSchema() {
    const P = S.publish;
    if (Array.isArray(P.fields) && P.fields.length) return { fields: P.fields, src: 'From the published OpenAPI schema' };
    if (P.openapi_url && !S.fake) {
      try {
        const r = await fetch(P.openapi_url, { mode: 'cors' });
        if (r.ok) {
          const f = fieldsFromOpenApi(await r.json());
          if (f.length) { P.fields = f; return { fields: f, src: 'From ' + P.openapi_url.replace(/^https?:\/\//, '') }; }
        }
      } catch { /* CORS or offline: fall back */ }
    }
    const sample = S.lastTry || lastGoodOutput();
    const f = sample ? fieldsFromSample(sample) : [];
    return { fields: f, src: 'Inferred from the verified output' };
  }

  function lastGoodOutput() {
    const r = [...S.runs].reverse().find((x) => x.exit_code === 0 && x.stdout);
    if (!r) return null;
    try { return JSON.parse(r.stdout); } catch { return null; }
  }

  function typeOf(v) {
    if (v === null) return 'null';
    if (Array.isArray(v)) return 'array';
    if (typeof v === 'number') return Number.isInteger(v) ? 'integer' : 'number';
    return typeof v;
  }

  function recordOf(v) {
    if (Array.isArray(v)) return v.find((x) => x && typeof x === 'object' && !Array.isArray(x)) || null;
    if (v && typeof v === 'object') {
      const arr = Object.values(v).find((x) => Array.isArray(x) && x.length && typeof x[0] === 'object');
      return arr ? arr[0] : v;
    }
    return null;
  }

  function fieldsFromSample(v) {
    const rec = recordOf(v);
    return rec ? Object.entries(rec).map(([k, x]) => ({ name: k, type: typeOf(x), required: x !== null })) : [];
  }

  function fieldsFromOpenApi(doc) {
    const refs = (doc.components && doc.components.schemas) || {};
    const deref = (sch, depth = 0) => {
      if (!sch || depth > 8) return sch || {};
      if (sch.$ref) return deref(refs[sch.$ref.split('/').pop()], depth + 1);
      if (sch.allOf && sch.allOf.length === 1) return deref(sch.allOf[0], depth + 1);
      return sch;
    };
    let sch = null;
    for (const [path, ops] of Object.entries(doc.paths || {})) {
      const op = ops && (ops.get || ops.post);
      const c = op && op.responses && (op.responses['200'] || op.responses[200]);
      const js = c && c.content && (c.content['application/json'] || Object.values(c.content)[0]);
      if (js && js.schema) { sch = js.schema; if (/run/.test(path)) break; }
    }
    sch = deref(sch);
    if (!sch) return [];
    if (sch.type === 'array' || sch.items) sch = deref(sch.items);
    const props = sch.properties || {};
    const keys = Object.keys(props);
    if (keys.length === 1 && deref(props[keys[0]]).type === 'array') sch = deref(deref(props[keys[0]]).items);
    const req = new Set(sch.required || []);
    return Object.entries(sch.properties || {}).map(([k, v]) => {
      const d = deref(v);
      const t = Array.isArray(d.type) ? d.type.join('|') : d.type || (d.anyOf ? d.anyOf.map((x) => deref(x).type).filter(Boolean).join('|') : 'any');
      return { name: k, type: t, required: req.size ? req.has(k) : true };
    });
  }

  // "One call instead of a hundred clicks": the agent's first run vs one call to the API.
  function renderSpeed() {
    const box = $('speedCard');
    if (!box || !S.publish) return;
    const end = S.tEnd || S.publishTs || Date.now() / 1000;
    const runS = S.t0 ? end - S.t0 : null;
    const runTxt = runS == null ? '…' : runS >= 60 ? `${Math.floor(runS / 60)}m ${Math.floor(runS % 60)}s` : `${runS.toFixed(1)} s`;
    if (S.tryMs == null) {
      box.innerHTML = `<div class="k">One call instead of a hundred clicks</div><div class="v">The agent took <b>${esc(runTxt)}</b> to learn this page once. Send a request to see how long a call takes now.</div>`;
      return;
    }
    const x = runS ? Math.round((runS * 1000) / Math.max(1, S.tryMs)) : null;
    box.innerHTML = `<div class="k">One call instead of a hundred clicks</div>${x ? `<div class="x">${x.toLocaleString()}× faster</div>` : ''}
      <div class="v">Agent run <b>${esc(runTxt)}</b> → API call <b>${esc(fmtMs(S.tryMs))}</b>, every time, with no LLM in the loop.</div>`;
  }

  function onHealth(d) {
    const chip = $('healthChip');
    if (chip && S.publish && (!d.api_id || d.api_id === (S.publish.api_id || S.publish.sandbox_id))) {
      chip.innerHTML = `<span class="chip ${d.ok ? 'ok' : 'accent'}" title="${esc((d.problems || []).join('; '))}"><span class="dot"></span>${d.ok ? 'Healthy' : 'Needs attention'} · checked ${esc(ago(d.last_verified || Date.now() / 1000))}</span>`;
    }
    if (catalogRows) {
      const row = catalogRows.find((r) => r.api_id === d.api_id);
      if (row) { Object.assign(row, { healthy: d.healthy ?? d.ok, last_verified: d.last_verified ?? Date.now() / 1000, problems: d.problems || [] }); renderCatalog(); }
    }
  }

  // ---------------------------------------------------------------------------
  // Catalog view
  // ---------------------------------------------------------------------------

  let catalogRows = null;
  let view = 'studio';
  const catBusy = new Set();

  function setView(v) {
    view = v;
    document.body.classList.toggle('view-catalog', v === 'catalog');
    document.querySelectorAll('[data-view-btn]').forEach((b) => b.classList.toggle('on', b.dataset.viewBtn === v));
    show('catalog', v === 'catalog');
    document.querySelectorAll('[data-view-btn]').forEach((b) => { if (b.dataset.viewBtn === v) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
    const q = v === 'catalog' ? '?view=catalog' : '';
    history.replaceState(null, '', location.pathname + q + location.hash);
    if (v === 'catalog') loadCatalog();
  }

  async function loadCatalog() {
    const grid = $('catGrid');
    if (!catalogRows) grid.innerHTML = [0, 1, 2].map(() => '<div class="card cat"><div class="skel" style="height:14px;width:40%"></div><div class="skel" style="height:26px;width:70%"></div><div class="skel" style="height:14px"></div><div class="skel" style="height:36px"></div></div>').join('');
    try {
      const r = await fetch('/api/catalog');
      if (!r.ok) throw new Error(r.status === 404 ? 'not available on this server yet' : r.statusText);
      catalogRows = await r.json();
      renderCatalog();
    } catch (err) {
      grid.innerHTML = `<div class="cat-empty empty-center"><div><div class="empty-title">Couldn't load your APIs</div><div class="empty-sub">${esc(err.message === 'Failed to fetch' ? 'The Unclick server is not reachable.' : err.message)}</div><button type="button" class="btn btn-ghost" data-cat-retry>Retry</button></div></div>`;
    }
  }

  function refreshCatalogCount() {
    fetch('/api/catalog').then((r) => (r.ok ? r.json() : null)).then((rows) => {
      if (!Array.isArray(rows)) return;
      catalogRows = rows;
      const c = $('catCount');
      c.textContent = rows.length;
      c.hidden = !rows.length;
      if (view === 'catalog') renderCatalog();
    }).catch(() => {});
  }

  function ago(ts) {
    if (!ts) return '';
    const t = typeof ts === 'number' ? ts * 1000 : Date.parse(ts);
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  }

  function renderCatalog() {
    const rows = catalogRows || [];
    const c = $('catCount');
    c.textContent = rows.length;
    c.hidden = !rows.length;
    const grid = $('catGrid');
    if (!rows.length) {
      grid.innerHTML = `<div class="cat-empty empty-center"><div><div class="empty-title">No APIs yet</div><div class="empty-sub">Every API you build shows up here, with health checks and self-heal.</div><button type="button" class="btn btn-primary" data-cat-build>Build your first API</button></div></div>`;
      return;
    }
    grid.innerHTML = rows.map((e, i) => {
      const busy = catBusy.has(e.api_id);
      const h = busy ? ['busy', busy === 'heal' ? 'Healing…' : 'Verifying…'] : e.healthy === true ? ['ok', 'Healthy'] : e.healthy === false ? ['bad', 'Needs attention'] : ['', 'Not verified yet'];
      let host = e.source_url || '';
      try { host = new URL(e.source_url).host.replace(/^www\./, '') + new URL(e.source_url).pathname.replace(/\/$/, ''); } catch { /* keep */ }
      const fields = Array.isArray(e.fields) ? e.fields : [];
      const problems = Array.isArray(e.problems) ? e.problems : [];
      return `<article class="card cat${e.replaced_by ? ' replaced' : ''}" style="animation-delay:${Math.min(i * 60, 600)}ms">
        <div class="cat-top"><span class="health ${h[0]}"><span class="dot"></span>${h[1]}</span>
          ${e.last_verified ? `<span>· verified ${esc(ago(e.last_verified))}</span>` : ''}
          <span class="grow"></span><span>${esc(ago(e.created_at))}</span></div>
        <div><div class="cat-name">${esc(e.name || e.api_id)}</div><div class="cat-src">${esc(host)}</div></div>
        ${e.task ? `<div class="cat-task">${esc(e.task)}</div>` : ''}
        ${fields.length ? `<div class="fields">${fields.slice(0, 8).map((f) => `<span class="field-chip"><span class="n">${esc(f.name)}</span><span class="t ${esc(String(f.type).split('|')[0])}">${esc(f.type)}</span></span>`).join('')}</div>` : ''}
        <div class="cat-url"><span title="${esc(e.api_url)}">${esc(e.api_url)}</span><button type="button" class="btn btn-ghost btn-sm" data-cat-copy="${esc(e.api_id)}">${svg(I.copy, 'ico-sm')}Copy</button></div>
        ${problems.length && e.healthy === false ? `<ul class="cat-problems">${problems.slice(0, 3).map((p) => `<li>${esc(p)}</li>`).join('')}</ul>` : ''}
        ${e.replaced_by ? `<div class="faint" style="font-size:13px">Replaced by <span class="mono">${esc(e.replaced_by)}</span> after a self-heal</div>` : ''}
        <div class="cat-actions">
          <button type="button" class="btn btn-ghost btn-sm" data-cat-verify="${esc(e.api_id)}" ${busy ? 'disabled' : ''}>${svg(I.check, 'ico-sm')}Verify</button>
          <button type="button" class="btn ${e.healthy === false ? 'btn-primary' : 'btn-ink'} btn-sm" data-cat-heal="${esc(e.api_id)}" ${busy ? 'disabled' : ''}>${svg(I.bolt, 'ico-sm')}Self-heal</button>
          <span class="grow"></span>
          ${e.mcp_url ? `<button type="button" class="btn btn-ghost btn-sm" data-cat-mcp="${esc(e.api_id)}" title="Copy the MCP endpoint">MCP</button>` : ''}
          ${e.docs_url ? `<a class="btn btn-ghost btn-sm" href="${esc(e.docs_url)}" target="_blank" rel="noopener">Docs ${svg(I.ext, 'ico-sm')}</a>` : ''}
        </div>
      </article>`;
    }).join('');
  }

  async function catVerify(id) {
    catBusy.add(id); renderCatalog();
    try {
      const r = await fetch(`/api/apis/${encodeURIComponent(id)}/verify`, { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || r.statusText);
      const row = (catalogRows || []).find((x) => x.api_id === id);
      if (row) Object.assign(row, { healthy: j.healthy ?? j.ok, last_verified: j.last_verified ?? Date.now() / 1000, problems: j.problems || [] });
      toast(j.ok ? 'Verified: output still matches the schema' : `Needs attention: ${(j.problems || [])[0] || 'verification failed'}`, 3500, j.ok ? 'ok' : 'bad');
    } catch (err) { toast('Verify failed: ' + err.message, 4000, 'bad'); }
    finally { catBusy.delete(id); renderCatalog(); }
  }

  async function catHeal(id) {
    catBusy.add(id); renderCatalog();
    try {
      const r = await fetch(`/api/apis/${encodeURIComponent(id)}/heal`, { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || r.statusText);
      if (j.run_id) {
        toast('Self-heal started: re-running the agent', 3000, 'ok');
        setView('studio');
        attach(j.run_id, undefined, j.mode ? { mode: j.mode } : undefined);
        revealStage();
        return;
      }
      toast(j.ok ? 'Healthy: nothing to heal' : 'Heal did not start', 3000, j.ok ? 'ok' : 'bad');
      refreshCatalogCount();
    } catch (err) { toast('Self-heal failed: ' + err.message, 4000, 'bad'); }
    finally { catBusy.delete(id); if (view === 'catalog') renderCatalog(); }
  }

  function renderSummary() {
    if (!S.publish) return;
    const box = $('summary');
    const end = S.tEnd || Date.now() / 1000;
    const secs = S.t0 ? Math.floor(end - S.t0) : 0;
    const time = secs >= 60 ? `${Math.floor(secs / 60)}<small>m</small> ${secs % 60}<small>s</small>` : `${secs}<small>s</small>`;
    const fixes = Math.max(0, S.codes.length - 1);
    const stats = [
      [time, S.done ? 'site → paid API' : 'so far', true],
      [String(S.codes.length), `attempt${S.codes.length === 1 ? '' : 's'}, ${fixes} fix${fixes === 1 ? '' : 'es'}`],
      [String(S.captures.length), 'JSON calls'],
      [String(S.sandboxes.size), 'sandboxes'],
    ];
    box.innerHTML = stats.map(([v, k, h]) => `<div class="stat${h ? ' hero-stat' : ''}"><div class="v">${v}</div><div class="k">${esc(k)}</div></div>`).join('');
    box.hidden = false;
  }

  async function tryIt() {
    const btn = $('tryBtn');
    btn.disabled = true;
    const out = $('tryOut');
    out.textContent = 'GET ' + S.publish.api_url + ' …';
    try {
      const r = await fetch('/api/try', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ run_id: S.runId, api_url: S.publish.api_url, key: boughtKey() }) });
      const j = await r.json();
      if (j.key_rejected) { try { localStorage.removeItem('unclick.key'); } catch { /* ignore */ } toast('Stored API key was rejected: used the demo key'); }
      const ok = j.status >= 200 && j.status < 300;
      if (ok && j.ms != null) { S.tryMs = j.ms; if (j.json && typeof j.json === 'object') S.lastTry = j.json; renderSpeed(); }
      $('tryMeta').innerHTML = `<span class="chip ${ok ? 'solid-ok' : 'solid-bad'}">${esc(j.status || 'ERR')}</span><span class="metric"><span class="v">${esc(fmtMs(j.ms))}</span></span>`;
      const body = j.json !== undefined ? j.json : j.error || j.detail;
      const txt = typeof body === 'string' ? body : JSON.stringify(body, null, 2);
      out.style.animation = 'none'; void out.offsetWidth; out.style.animation = 'slideUp .3s ease-out both';
      try { out.innerHTML = hljs.highlight(txt, { language: 'json' }).value; } catch { out.textContent = txt; }
    } catch (err) {
      out.textContent = 'Request failed: ' + err.message;
    } finally { btn.disabled = false; }
  }

  async function wakeTest() {
    const btn = $('wakeBtn');
    btn.disabled = true;
    $('tryMeta').innerHTML = '<span class="chip info pulse"><span class="dot"></span>waking…</span>';
    try {
      const r = await fetch('/api/wake', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ run_id: S.runId, api_url: S.publish.api_url }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.statusText);
      if (j.local) $('tryMeta').innerHTML = `<span class="metric" title="Local sandbox mode: the API never pauses"><span class="k">local · /health</span><span class="v">${esc(fmtMs(j.wake_ms))}</span></span>`;
      else showWake(j.wake_ms);
    } catch (err) {
      $('tryMeta').innerHTML = '<span class="chip bad">wake failed</span>';
      toast('Wake test failed: ' + err.message, 4000, 'bad');
    } finally { btn.disabled = false; }
  }

  let pollT = null;
  function startStatusPoll() {
    clearInterval(pollT);
    if (S.fake || S.mode === 'replay') return;
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
    if (m) m.innerHTML = `<span class="metric"><span class="k">paused → awake</span><span class="v" style="color:rgb(var(--info))">${esc(fmtMs(ms))}</span></span>`;
  }

  function onBilling(d, ev) {
    revealCard('payCard');
    const B = S.billing;
    if (d.calls_remaining != null) {
      B.calls = d.calls_remaining;
      const el = $('callsLeft');
      el.textContent = B.calls;
      el.classList.remove('bump'); void el.offsetWidth; el.classList.add('bump');
    }
    if (d.api_key) B.apiKey = d.api_key;
    if (d.event === 'paid' && d.api_key) { try { localStorage.setItem('unclick.key', d.api_key); } catch { /* ignore */ } }
    const cents = d.amount_cents != null ? `$${(d.amount_cents / 100).toFixed(2)}` : '';
    const when = `<span class="when">${clock(ev.ts)}</span>`;
    if (d.event === 'call_charged' && d.metered === false) {
      B.last = `<span class="chip warn">demo key</span><span>1 call used</span><span class="amt faint">unmetered</span>${when}`;
    } else if (d.event === 'call_charged' && d.calls_remaining === 0 && !d.amount_cents) {
      B.last = `<span class="chip bad">402</span><span>Out of calls</span><span class="amt"></span>${when}`;
    } else if (d.event === 'call_charged') {
      B.last = `<span class="chip stripe">usage</span><span>1 call charged</span><span class="amt">${esc(cents)}</span>${when}`;
    } else if (d.event === 'paid') {
      B.last = `<span class="chip ok">paid</span><span>Checkout complete</span><span class="amt">${esc(cents)}</span>${when}`;
    } else if (d.event === 'checkout_created') {
      B.last = `<span class="chip">checkout</span><span>Session created</span><span class="amt"></span>${when}`;
    }
    if (B.last) {
      const lc = $('lastCharge');
      lc.innerHTML = B.last;
      lc.className = 'charge'; void lc.offsetWidth; lc.className = 'charge has';
    }
    B.feed.unshift(`<div><span class="when">${clock(ev.ts)}</span><span class="m">${esc(ev.message)}</span></div>`);
    B.feed = B.feed.slice(0, 2);
    renderChargeNote();
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
      toast(j.fake ? 'Test purchase simulated: +100 calls' : 'Checkout created', 2600, 'ok');
    } catch (err) {
      toast('Checkout failed: ' + err.message, 4000, 'bad');
    } finally { renderBillingMode(); if (!stripeOff()) btn.disabled = false; }
  }

  // ---------------------------------------------------------------------------
  // Timeline + done
  // ---------------------------------------------------------------------------

  const HL_KINDS = new Set(['status', 'endpoint', 'publish', 'done']);
  function addLog(ev) {
    const t = S.t0 != null ? ev.ts - S.t0 : 0;
    const warn = ev.kind === 'log' && ev.data && ev.data.level === 'warn';
    const el = document.createElement('div');
    el.className = 'logrow' + (ev.kind === 'error' ? ' err' : warn ? ' warn' : HL_KINDS.has(ev.kind) ? ' hl' : '');
    el.innerHTML = `<span class="t">+${t.toFixed(1)}s</span><span class="k">${esc(ev.kind)}</span><span class="msg">${esc(ev.message)}</span>`;
    const pane = $('pane-log');
    const atBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 60;
    pane.appendChild(el);
    if (atBottom) pane.scrollTop = pane.scrollHeight;
  }

  function onDone(d, ev) {
    const top = $('ticker').firstElementChild;
    if (top && top.classList.contains('now')) settle(top);
    $('activityTitle').textContent = S.failed ? 'Unclick stopped' : 'Unclick finished';
    S.done = true;
    S.tEnd = ev.ts;  // stays right when a finished run is replayed
    $('runDot').className = 'run-dot ' + (S.failed ? 'bad' : 'ok');
    renderStepper();
    renderSummary();
    renderSpeed();
    setBusy(false);
    $('runbarK').textContent = S.failed ? 'Stopped' : S.publish ? 'Live' : 'Finished';
    if (!S.failed) toast(d.summary ? `Done: ${d.summary}` : 'Done', 4000, 'ok');
  }

  // ---------------------------------------------------------------------------
  // Page state: empty (landing) → running → result
  // ---------------------------------------------------------------------------

  function setState(st) {
    document.body.classList.remove('state-empty', 'state-running', 'state-result');
    document.body.classList.add('state-' + st);
    const on = st !== 'empty';
    show('runbar', on); show('stepperWrap', on); show('mainContent', on); show('runMeta', on);
    show('builtH', st === 'result');
    if (on) {
      $('runUrl').textContent = $('urlIn').value.trim();
      $('runTask').textContent = $('taskIn').value.trim();
      $('runbarK').textContent = st === 'result' ? 'Live' : 'Building';
    }
    updateRail();
  }

  function revealCard(id) {
    const c = $(id);
    if (c && c.hidden) { c.hidden = false; updateRail(); }
  }
  function updateRail() {
    const rail = document.querySelector('.rail');
    rail.classList.toggle('empty', ![...rail.children].some((c) => !c.hidden));
  }
  function revealAgent() {
    const a = $('agentCard');
    if (!a.hidden) return;
    a.hidden = false;
    // Bring the code into view if it landed below the fold.
    setTimeout(() => {
      const r = a.getBoundingClientRect();
      if (r.top > innerHeight - 120 && !document.body.classList.contains('state-result')) a.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 400);
  }

  function buildAnother() {
    if (es) { es.close(); es = null; }
    clearInterval(timerInt);
    S = fresh();
    resetUI();
    history.replaceState(null, '', location.pathname + (view === 'catalog' ? '?view=catalog' : ''));
    setView('studio');
    setState('empty');
    setBusy(false);
    window.scrollTo({ top: 0 });
    setTimeout(() => $('urlIn').focus(), 50);
  }

  // ---------------------------------------------------------------------------
  // UI plumbing
  // ---------------------------------------------------------------------------

  function showRTab(name) {
    document.querySelectorAll('[data-rtab]').forEach((b) => b.classList.toggle('on', b.dataset.rtab === name));
    document.querySelectorAll('.pane').forEach((p) => { p.hidden = p.id !== 'pane-' + name; });
    if (name === 'log') { const pl = $('pane-log'); pl.scrollTop = pl.scrollHeight; }
  }

  function setFollow(on) {
    follow = on;
    $('followBtn').classList.toggle('on', on);
  }

  const LOOP = [
    ['Write', 'client.py from the captured calls'],
    ['Run', 'it in a CreateOS sandbox'],
    ['Read', 'the traceback, diff vs the screen'],
    ['Fix', 'and rerun until it matches'],
  ];
  const CODE_EMPTY = `<div class="empty-center"><div>
    <div class="loop-k">The agent's code loop</div>
    <div class="loop">${LOOP.map(([t, d], k) => `<div class="loop-step"><div class="n">0${k + 1}</div><div class="t">${t}</div><div class="d">${d}</div></div>${k < LOOP.length - 1 ? `<span class="loop-arrow">${svg('<path d="M5 12h14M13 6l6 6-6 6"/>', 'ico-sm')}</span>` : ''}`).join('')}</div>
    <div class="empty-sub" style="margin-top:14px">client.py appears here, with a diff for every fix</div>
  </div></div>`;

  const empty = (icon, title, sub) => `<div class="empty-center"><div><div class="empty-icon">${svg(icon)}</div><div class="empty-title">${title}</div><div class="empty-sub">${sub}</div></div></div>`;

  function resetUI() {
    $('ticker').innerHTML = '';
    show('activity', false);
    $('activityTitle').textContent = 'Unclick working…';
    $('activityPhase').textContent = '';
    $('liveFrame').src = 'about:blank';
    show('popOut', false);
    show('deskId', false);
    $('shotImg').removeAttribute('src');
    $('capList').innerHTML = empty(I.net, 'Listening to the network', 'JSON responses Chrome receives show up here');
    $('capCount').textContent = '0';
    show('endpointBox', false);
    show('netPanel', true);
    show('summary', false);
    ['sbCard', 'egCard', 'payCard', 'agentCard', 'apiPage'].forEach((id) => show(id, false));
    $('apiPage').innerHTML = '';
    $('capList').innerHTML = '<div class="skel skel-row"></div><div class="skel skel-row" style="width:80%"></div><div class="skel skel-row" style="width:65%"></div>';
    $('stepPill').textContent = 'Starting…';
    updateRail();
    $('egressHosts').innerHTML = '<span class="empty">Locked to the target site when the run starts</span>';
    $('egressReason').textContent = '';
    $('egressMode').textContent = 'not set';
    $('egressMode').className = 'chip';
    $('sbList').innerHTML = '<div class="empty">Desktop, runner and API sandboxes appear here as the agent boots them</div>';
    $('sbCount').textContent = '0 active';
    $('sbCount').className = 'chip';
    show('forkBox', false);
    show('pauseBox', false);
    $('codeView').innerHTML = CODE_EMPTY;
    $('attemptPills').innerHTML = '';
    $('codeMeta').innerHTML = '';
    ['runCount', 'verifyCount'].forEach((id) => { $(id).textContent = '0'; });
    $('pane-runs').innerHTML = empty(I.term, 'No runs yet', 'Each attempt runs in a sandbox: stdout, stderr, exit code');
    $('pane-verify').innerHTML = empty(I.check, 'Nothing to verify yet', 'Output is checked against what Chrome showed');
    $('pane-log').innerHTML = '';
    $('callsLeft').textContent = '—';
    $('lastCharge').className = 'charge';
    $('lastCharge').textContent = 'No charges yet';
    $('billingFeed').innerHTML = '';
    $('timer').textContent = '00:00';
    $('runDot').className = 'run-dot';
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
  function toast(msg, ms = 2600, kind = '') {
    const t = $('toast');
    const icon = kind === 'ok' ? `<span style="color:rgb(var(--ok))">${svg(I.check)}</span>` : kind === 'bad' ? `<span style="color:rgb(var(--bad))">${svg(I.x)}</span>` : '';
    t.innerHTML = icon + `<span>${esc(msg)}</span>`;
    t.hidden = false;
    t.style.animation = 'none'; void t.offsetWidth; t.style.animation = '';
    clearTimeout(toastT);
    toastT = setTimeout(() => { t.hidden = true; }, ms);
  }

  async function copy(text, btn) {
    try { await navigator.clipboard.writeText(text); } catch {
      const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
    }
    const old = btn.innerHTML; btn.innerHTML = `${svg(I.check, 'ico-sm')}Copied`;
    setTimeout(() => { btn.innerHTML = old; }, 1200);
  }

  async function startReplay(name, speed) {
    try {
      const r = await fetch('/api/runs', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ replay: name, speed: speed ? Number(speed) : undefined }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.statusText);
      attach(j.run_id, j.fake, j);
    } catch (err) { toast('Replay failed: ' + err.message, 5000, 'bad'); }
  }

  // Wire up
  const qs0 = new URLSearchParams(location.search);
  $('runForm').addEventListener('submit', startRun);
  $('buyBtn').addEventListener('click', buy);
  document.querySelectorAll('[data-view-btn]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.viewBtn)));
  document.querySelectorAll('.example').forEach((b) => b.addEventListener('click', () => {
    $('urlIn').value = b.dataset.url;
    $('taskIn').value = b.dataset.task;
    $('goBtn').focus();
  }));
  $('newBtn').addEventListener('click', buildAnother);
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!$('toast').hidden) { $('toast').hidden = true; return; }
    if (view === 'catalog') setView('studio');
  });
  $('catRefresh').addEventListener('click', loadCatalog);
  $('catGrid').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.hasAttribute('data-cat-retry')) { loadCatalog(); return; }
    if (b.hasAttribute('data-cat-build')) { buildAnother(); return; }
    const row = (id) => (catalogRows || []).find((x) => x.api_id === id) || {};
    if (b.dataset.catVerify) catVerify(b.dataset.catVerify);
    else if (b.dataset.catHeal) catHeal(b.dataset.catHeal);
    else if (b.dataset.catCopy) copy(row(b.dataset.catCopy).api_url || '', b);
    else if (b.dataset.catMcp) copy(row(b.dataset.catMcp).mcp_url || '', b);
  });
  refreshCatalogCount();
  if (qs0.get('view') === 'catalog') setView('catalog');
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
    if (c.billing) {
      fetch('/api/billing/config').then((r) => r.json()).then((b) => { billingCfg = b; renderBillingMode(); }).catch(() => {});
    }
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
  if (qs.has('paid')) setTimeout(() => toast('Stripe test payment complete: API key issued', 4500, 'ok'), 600);
  if (qs.has('canceled')) setTimeout(() => toast('Checkout canceled', 3000), 600);
  if (qs.has('buy')) setTimeout(buy, 400);
})();

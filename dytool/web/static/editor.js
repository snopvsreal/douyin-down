/* Cửa sổ Mẫu edit của kênh (thử nghiệm, kiểu CapCut).
   State S = mẫu (vtemplate) + S.base (lật / màu phủ / nghiêng của kênh). Mọi ô có data-k="đường.dẫn" tự gắn 2 chiều.
   Xem trước = CSS gần đúng; Render thử = FFmpeg thật ở máy chủ. Tự lưu 0,5 s sau lần sửa cuối. */
(() => {
  'use strict';
  const ED = window.__ED, $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const S = structuredClone(ED.tpl); S.base = structuredClone(ED.base);
  const root = $('#ed'), frame = $('#ed-frame'), stage = $('#ed-stage');
  const V = $('#ed-v'), BG = $('#ed-bgv');
  let sel = null;                 // id lớp đang chọn
  let srcDur = 10, vw = 1080, vh = 1920, hasVideo = false;
  let t = 0, playing = false, lastTs = 0;
  const music = new Audio();

  // ---------- đường dẫn state ----------
  const get = (p) => p.split('.').reduce((o, k) => (o == null ? o : o[k]), S);
  const set = (p, v) => { const ks = p.split('.'), last = ks.pop(); ks.reduce((o, k) => o[k], S)[last] = v; };
  const fmt = (v, f) => {
    v = Number(v);
    switch (f) {
      case 's': return v.toFixed(1).replace('.', ',') + ' s';
      case 'x': return v.toFixed(2).replace('.', ',') + '×';
      case 'pct': return Math.round(v * 100) + '%';
      case 'deg': return (v > 0 ? '+' : '') + v + '°';
      case 'K': return v + ' K';
      case 'signed': return (v > 0 ? '+' : '') + Math.round(v * 100);
      default: return String(Math.round(v * 100) / 100).replace('.', ',');
    }
  };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const toast = (msg, err) => {
    const el = $('#ed-toast'); el.textContent = msg; el.className = 'ed-toast' + (err ? ' err' : ''); el.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => { el.hidden = true; }, 4000);
  };

  // ---------- tự lưu ----------
  let saveT = null, saving = false, again = false;
  const saveEl = $('#ed-save');
  function changed() {
    saveEl.textContent = 'Chưa lưu…'; saveEl.className = 'ed-save wait';
    clearTimeout(saveT); saveT = setTimeout(save, 500);
    refreshUI(); draw(true);
  }
  async function save() {
    if (saving) { again = true; return; }
    saving = true; saveEl.textContent = 'Đang lưu…';
    const { base, ...tpl } = S;
    try {
      const r = await fetch(`/pages/${ED.pid}/tpl`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tpl, base }) });
      if (!r.ok) throw new Error(await r.text());
      const j = await r.json();
      // giữ id lớp phía máy chủ (lớp rỗng bị bỏ) nhưng không đè ô đang gõ
      if (!again) { S.layers = j.tpl.layers.length === S.layers.length ? S.layers : j.tpl.layers; }
      saveEl.textContent = 'Đã lưu'; saveEl.className = 'ed-save ok';
    } catch (e) { saveEl.textContent = 'Lỗi lưu'; saveEl.className = 'ed-save err'; toast('Không lưu được: ' + e.message, true); }
    saving = false;
    if (again) { again = false; save(); }
  }
  window.addEventListener('beforeunload', () => { if (saveT && saveEl.textContent !== 'Đã lưu') save(); });

  // ---------- gắn ô <-> state ----------
  function bindAll() {
    $$('[data-k]').forEach((el) => {
      const p = el.dataset.k;
      const ev = el.type === 'checkbox' || el.tagName === 'SELECT' || el.type === 'color' ? 'change' : 'input';
      el.addEventListener(ev, () => {
        let v = el.type === 'checkbox' ? el.checked : el.value;
        if (el.type === 'range' || el.type === 'number') v = Number(v);
        set(p, v); changed();
      });
      if (el.type === 'color') el.addEventListener('input', () => { set(p, el.value); draw(true); changed(); });
    });
    $$('[data-choice]').forEach((g) => g.addEventListener('click', (e) => {
      const b = e.target.closest('[data-v]'); if (!b) return;
      set(g.dataset.choice, b.dataset.v); changed(); layout();
    }));
    $$('[data-trans]').forEach((g) => g.addEventListener('click', (e) => {
      const b = e.target.closest('[data-v]'); if (!b) return;
      set(g.dataset.trans + '.transition', b.dataset.v); changed();
      // xem ngay chuyển cảnh: tua tới trước điểm chuyển 0,6 s
      const k = g.dataset.trans, tl = timeline();
      seek(k === 'intro' ? Math.max(0, tl.mainStart - 0.6) : Math.max(0, tl.outStart - 0.6)); play(true);
    }));
  }
  function refreshUI() {
    $$('[data-k]').forEach((el) => {
      if (el === document.activeElement && el.type !== 'range') return;
      const v = get(el.dataset.k);
      if (el.type === 'checkbox') el.checked = !!v; else if (v != null) el.value = v;
    });
    $$('[data-o]').forEach((o) => { o.textContent = fmt(get(o.dataset.o), o.dataset.fmt); });
    $$('[data-choice]').forEach((g) => $$('[data-v]', g).forEach((b) => b.setAttribute('aria-pressed', String(get(g.dataset.choice) === b.dataset.v))));
    $$('[data-trans]').forEach((g) => $$('[data-v]', g).forEach((b) => b.setAttribute('aria-pressed', String(get(g.dataset.trans + '.transition') === b.dataset.v))));
    for (const k of ['intro', 'outro', 'progress']) {
      $$(`[data-show-on="${k}"]`).forEach((e) => { e.hidden = !S[k].enabled; });
      $$(`[data-show-off="${k}"]`).forEach((e) => { e.hidden = !!S[k].enabled; });
    }
    for (const k of ['intro', 'outro']) {
      const vid = isVidFile(S[k].file);
      $$(`[data-show-vid="${k}"]`).forEach((e) => { e.hidden = !vid; });
      $$(`[data-show-novid="${k}"]`).forEach((e) => { e.hidden = vid; });
      $$(`[data-show-aud="${k}"]`).forEach((e) => { e.hidden = !S[k].audio; });
      $$(`[data-show-file="${k}"]`).forEach((e) => { e.hidden = !S[k].file; });
    }
    $$('[data-file]').forEach((w) => {
      const v = get(w.dataset.file);
      $('[data-fname]', w).textContent = v ? (fileLabels[v] || 'Đã chọn file') : 'Chưa chọn';
      const c = $('[data-clear]', w); if (c) c.hidden = !v;
    });
    $$('.ed-rail [data-tab]').forEach((b) => {
      const k = b.dataset.tab; b.classList.toggle('ed-on', k === 'intro' ? S.intro.enabled || S.outro.enabled :
        k === 'music' ? !!S.music.file : k === 'text' ? S.layers.length > 0 : false);
    });
    $$('[data-segdot]').forEach((i) => { i.hidden = !S[i.dataset.segdot].enabled; });
    renderLayers();
  }
  const fileLabels = JSON.parse(localStorage.getItem('dy-ed-files') || '{}');
  const fileDur = {};
  const isVidFile = (f) => !!f && /\.(mp4|mov|webm)$/.test(f), isImgFile = (f) => !!f && /\.(png|jpe?g|webp)$/.test(f);

  // ---------- tab ----------
  $$('.ed-rail [data-tab]').forEach((b) => b.addEventListener('click', () => openTab(b.dataset.tab)));
  $$('[data-seg]').forEach((b) => b.addEventListener('click', () => openTab(b.dataset.seg)));
  function openTab(k) {
    const rail = k === 'outro' ? 'intro' : k;        // Intro & Outro chung 1 mục, chuyển bằng nút trong bảng
    $$('.ed-rail [data-tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === rail)));
    $$('.ed-panel').forEach((p) => { p.hidden = p.dataset.panel !== k; });
    if (k === 'intro' && S.intro.enabled && t >= S.intro.duration) seek(Math.min(1.2, S.intro.duration * 0.6));
    if (k === 'outro' && S.outro.enabled && t < timeline().outStart) seek(timeline().outStart + Math.min(1.2, S.outro.duration * 0.6));
  }

  // ---------- tải file ----------
  const fileIn = $('#ed-file');
  function pickFile(accept) {
    return new Promise((res) => {
      fileIn.accept = accept || ''; fileIn.value = '';
      fileIn.onchange = async () => {
        const f = fileIn.files[0]; if (!f) return res(null);
        const fd = new FormData(); fd.append('file', f);
        toast('Đang tải ' + f.name + '…');
        try {
          const r = await fetch('/tpl/asset', { method: 'POST', body: fd });
          if (!r.ok) throw new Error(await r.text());
          const j = await r.json(); fileLabels[j.name] = j.label;
          localStorage.setItem('dy-ed-files', JSON.stringify(fileLabels));
          $('#ed-toast').hidden = true; res(j);
        } catch (e) { toast('Không tải lên được: ' + e.message, true); res(null); }
      };
      fileIn.click();
    });
  }
  $$('[data-file]').forEach((w) => {
    $('[data-pick]', w).addEventListener('click', async () => {
      const j = await pickFile(w.dataset.accept); if (!j) return;
      set(w.dataset.file, j.name);
      const seg = w.dataset.file.split('.')[0];
      if ((seg === 'intro' || seg === 'outro') && j.kind === 'video' && j.dur > 0) {
        fileDur[j.name] = j.dur; S[seg].duration = Math.min(30, Math.max(0.5, Math.round(j.dur * 10) / 10));
        toast(`Độ dài ${seg} = ${S[seg].duration.toFixed(1).replace('.', ',')} s theo video` + (j.dur > 30 ? ' (tối đa 30 s)' : ''));
      }
      changed(); loadSegMedia();
      if (w.dataset.file === 'music.file') loadMusic();
    });
    $('[data-clear]', w)?.addEventListener('click', () => {
      set(w.dataset.file, ''); changed(); loadSegMedia(); if (w.dataset.file === 'music.file') loadMusic();
    });
  });

  // ---------- video mẫu ----------
  const sampleSel = $('#ed-sample');
  let lastSample = sampleSel.value;
  sampleSel.addEventListener('change', async () => {
    if (sampleSel.value === '__upload') {
      const j = await pickFile('video/*');
      if (!j) { sampleSel.value = lastSample; return; }
      const o = new Option('Video tải lên · ' + j.label.slice(0, 30), 'a:' + j.name); o.dataset.url = '/tpl/asset/' + j.name;
      sampleSel.add(o, 0); sampleSel.value = o.value;
    }
    lastSample = sampleSel.value; loadSample();
  });
  $('#ed-up-sample').addEventListener('click', () => { sampleSel.value = '__upload'; sampleSel.dispatchEvent(new Event('change')); });
  function loadSample() {
    const o = sampleSel.selectedOptions[0];
    const url = o && o.value !== '__upload' ? o.dataset.url : '';
    $('#ed-empty').hidden = !!url; hasVideo = !!url;
    if (!url) { layout(); draw(true); return; }
    V.src = url; BG.src = url;
    V.addEventListener('loadedmetadata', () => {
      srcDur = V.duration || 10; vw = V.videoWidth || 1080; vh = V.videoHeight || 1920;
      $('#r-main\\.trim_start').max = $('#r-main\\.trim_end').max = Math.max(1, Math.floor(srcDur / 2));
      layout(); seek(Math.min(t, timeline().total)); grabThumb();
    }, { once: true });
  }

  // ---------- dòng thời gian (giống vtemplate.build) ----------
  function timeline() {
    const m = S.main;
    const t0 = Math.min(m.trim_start, Math.max(0, srcDur - 1)), t1 = Math.max(t0 + 0.5, srcDur - m.trim_end);
    const D = (t1 - t0) / m.speed;
    let mainStart = 0, tdIn = 0, tdOut = 0;
    if (S.intro.enabled) {
      tdIn = S.intro.transition === 'none' ? 0 : Math.min(S.intro.trans_dur, S.intro.duration / 2, D / 2);
      mainStart = S.intro.duration - tdIn;
    }
    const mainEnd = mainStart + D;
    let outStart = mainEnd, total = mainEnd;
    if (S.outro.enabled) {
      tdOut = S.outro.transition === 'none' ? 0 : Math.min(S.outro.trans_dur, S.outro.duration / 2, D / 2);
      outStart = mainEnd - tdOut; total = outStart + S.outro.duration;
    }
    return { t0, t1, D, mainStart, mainEnd, outStart, total, tdIn, tdOut };
  }

  // ---------- bố cục khung ----------
  function canvasRatio() {
    const r = S.canvas.ratio;
    if (r === 'keep') return vw / vh;
    const [a, b] = r.split(':').map(Number); return a / b;
  }
  let FH = 640;
  function layout() {
    const box = stage.getBoundingClientRect(), ctlH = 52;
    const aw = box.width - 32, ah = box.height - ctlH - 24, r = canvasRatio();
    let w = aw, h = aw / r; if (h > ah) { h = ah; w = ah * r; }
    frame.style.width = Math.round(w) + 'px'; frame.style.height = Math.round(h) + 'px';
    FH = h;
    const fit = S.canvas.fit, sameRatio = Math.abs(vw / vh - r) < 0.01;
    V.style.objectFit = fit === 'crop' ? 'cover' : 'contain';
    BG.hidden = sameRatio || fit !== 'blur';
    frame.style.background = fit === 'black' || sameRatio ? '#000' : '#111';
    draw(true);
  }
  new ResizeObserver(layout).observe(stage);

  // ---------- vẽ 1 khung ----------
  const segEls = { intro: $('#ed-seg-intro'), outro: $('#ed-seg-outro') };
  function transStyle(el, kind, p, incoming) {
    // p 0..1 = tiến độ chuyển cảnh; incoming = phần tử đang hiện ra
    el.style.opacity = ''; el.style.clipPath = ''; el.style.transform = ''; el.style.filter = '';
    const q = incoming ? p : 1 - p;
    switch (kind) {
      case 'wipeleft': if (incoming) el.style.clipPath = `inset(0 0 0 ${100 - p * 100}%)`; break;
      case 'wiperight': if (incoming) el.style.clipPath = `inset(0 ${100 - p * 100}% 0 0)`; break;
      case 'wipeup': if (incoming) el.style.clipPath = `inset(${100 - p * 100}% 0 0 0)`; break;
      case 'slideleft': el.style.transform = `translateX(${incoming ? (1 - p) * 100 : -p * 100}%)`; break;
      case 'slideup': el.style.transform = `translateY(${incoming ? (1 - p) * 100 : -p * 100}%)`; break;
      case 'smoothleft': el.style.transform = `translateX(${incoming ? (1 - p) * 100 : -p * 100}%)`; el.style.opacity = q; break;
      case 'circleopen': if (incoming) el.style.clipPath = `circle(${p * 75}% at 50% 50%)`; break;
      case 'circleclose': if (!incoming) el.style.clipPath = `circle(${(1 - p) * 75}% at 50% 50%)`; break;
      case 'zoomin': if (!incoming) { el.style.transform = `scale(${1 + p})`; el.style.opacity = 1 - p; } break;
      case 'pixelize': el.style.filter = `blur(${Math.sin(p * Math.PI) * 6}px)`; el.style.opacity = q; break;
      case 'fadeblack': case 'fadewhite': el.style.opacity = incoming ? Math.max(0, p * 2 - 1) : Math.max(0, 1 - p * 2); break;
      default: el.style.opacity = q;
    }
  }
  function textAnim(el, anim, local, a, b, base) {
    const d = 0.35;
    let al = base;
    if (anim !== 'none') al = base * Math.min(1, Math.max(0, Math.min((local - a) / d, (b - local) / d)));
    el.style.opacity = local < a || local > b ? 0 : al;
    let tx = 0, ty = 0;
    const k = Math.max(0, 1 - (local - a) / 0.4);
    if (anim === 'slide_up') ty = 0.05 * k * FH;
    if (anim === 'slide_left') tx = -0.25 * k * k * frame.clientWidth;
    el.style.translate = `${tx}px ${ty}px`;
  }
  function styleText(el, o) {
    const fs = o.size * FH;
    el.style.fontSize = fs + 'px';
    el.style.fontFamily = o.font === 'bold' ? 'EDBold' : 'EDXBold';
    el.style.color = o.color;
    el.style.webkitTextStroke = o.stroke_w ? `${2 * fs * o.stroke_w}px ${o.stroke}` : '0';
    if (o.box) {
      const h = Math.round(o.box_opacity * 255).toString(16).padStart(2, '0');
      el.style.background = o.box_color + h; el.style.padding = `${fs * 0.35}px`;
    } else { el.style.background = 'none'; el.style.padding = '0'; }
  }
  function drawSeg(k, tl) {
    const el = segEls[k], s = S[k];
    let show = false, local = 0, p = 1, incoming = true;
    if (s.enabled) {
      if (k === 'intro') {
        show = t < S.intro.duration; local = t;
        if (tl.tdIn && t > tl.mainStart) { p = (t - tl.mainStart) / tl.tdIn; incoming = false; }
        else p = -1;
      } else {
        show = t >= tl.outStart; local = t - tl.outStart;
        p = tl.tdOut && local < tl.tdOut ? local / tl.tdOut : -1;
      }
    }
    el.hidden = !show;
    if (!show) { const v = $('video', el); if (!v.paused) v.pause(); return; }
    if (p >= 0) transStyle(el, s.transition, p, incoming); else { el.style.opacity = ''; el.style.clipPath = ''; el.style.transform = ''; el.style.filter = ''; }
    const f = s.file, img = isImgFile(f), vid = isVidFile(f);
    el.style.backgroundColor = s.bg;
    el.style.backgroundImage = img ? `url(/tpl/asset/${f})` : '';
    el.style.backgroundSize = s.fit === 'crop' ? 'cover' : 'contain';
    const sbg = $('.sg-bg', el);            // nền mờ (vừa khung, nền mờ) — ảnh: chính ảnh đó; video: khung đầu
    sbg.hidden = !(f && s.fit === 'blur');
    if (!sbg.hidden && img) sbg.style.backgroundImage = `url(/tpl/asset/${f})`;
    const v = $('video', el);
    if (vid && v.readyState >= 1) {
      v.style.objectFit = s.fit === 'crop' ? 'cover' : 'contain';
      const want = Math.min(local, Math.max(0, v.duration - 0.05));     // ngắn hơn đoạn: giữ khung cuối
      const run = playing && local < v.duration - 0.05;
      if (!run || Math.abs(v.currentTime - want) > 0.3) { if (Math.abs(v.currentTime - want) > 0.04) v.currentTime = want; }
      v.muted = !s.audio; v.volume = Math.min(1, s.volume);
      if (run) { if (v.paused) v.play().catch(() => {}); } else if (!v.paused) v.pause();
    }
    const tt = $('.sg-t', el), ss = $('.sg-s', el);
    const hasT = !!s.title.trim(), hasS = !!s.sub.trim();
    tt.textContent = s.title; ss.textContent = s.sub;
    tt.hidden = !hasT; ss.hidden = !hasS;
    styleText(tt, { size: s.size, font: 'extrabold', color: s.color, stroke: '#000000', stroke_w: 0.06, box: false });
    styleText(ss, { size: s.size * 0.5, font: 'bold', color: s.color, stroke: '#000000', stroke_w: 0, box: true, box_color: s.accent, box_opacity: 0.9 });
    tt.style.top = (hasS ? 0.45 : 0.5) * 100 + '%';
    ss.style.top = (hasT ? 0.45 + s.size * 0.9 : 0.5) * 100 + '%';
    textAnim(tt, s.anim, local, 0.15, s.duration, 1); textAnim(ss, s.anim, local, 0.4, s.duration, 1);
  }
  function draw(still) {
    const tl = timeline(), m = S.main;
    const local = t - tl.mainStart, inMain = t >= tl.mainStart && t < tl.mainEnd;
    // video chính
    if (hasVideo) {
      const want = tl.t0 + Math.max(0, Math.min(tl.D, local)) * m.speed;
      if (!playing || !inMain || V.paused || Math.abs(V.currentTime - want) > 0.3) {
        if (Math.abs(V.currentTime - want) > 0.05) V.currentTime = want;
      }
      if (Math.abs(BG.currentTime - V.currentTime) > 0.3) BG.currentTime = V.currentTime;
      if (playing && inMain) { V.playbackRate = m.speed; if (V.paused) V.play().catch(() => {}); if (!BG.hidden && BG.paused) BG.play().catch(() => {}); }
      else { if (!V.paused) V.pause(); if (!BG.paused) BG.pause(); }
      V.volume = Math.min(1, m.volume);
    }
    // video mẫu đã edit = đã có sẵn lật / màu phủ / nghiêng -> chỉ áp lại khi xem video tải lên (bản gốc)
    const b = sampleSel.value.startsWith('a:') ? S.base : { hflip: false, rotate_deg: 0, tint_color: '#000000', tint_opacity: 0 };
    const z = 1 + m.zoom * Math.max(0, Math.min(1, local / tl.D));
    const rot = b.rotate_deg ? `rotate(${b.rotate_deg}deg) scale(${1 + Math.abs(b.rotate_deg) * 0.035})` : '';
    $('#ed-mainwrap').style.transform = `scale(${z}) ${b.hflip ? 'scaleX(-1)' : ''} ${rot}`;
    BG.style.transform = b.hflip ? 'scaleX(-1)' : '';
    $('#ed-mainwrap').style.filter = `brightness(${1 + m.brightness * 1.6}) contrast(${m.contrast}) saturate(${m.saturation}) hue-rotate(${m.hue}deg)`;
    const tint = $('#ed-tint'); tint.style.background = b.tint_color; tint.style.opacity = b.tint_opacity;
    const temp = $('#ed-temp'), dk = (m.temperature - 6500) / 3500;
    temp.style.background = dk < 0 ? '#ff8a2a' : '#3a8bff'; temp.style.opacity = Math.min(0.35, Math.abs(dk) * 0.3);
    $('#ed-vig').hidden = !m.vignette;
    const gr = $('#ed-grain'); gr.style.opacity = m.grain / 70; gr.hidden = !m.grain;
    // hiện / tối dần đầu cuối (không có intro / outro)
    let fade = 1;
    if (!S.intro.enabled && m.fade_in && local < m.fade_in) fade = Math.max(0, local / m.fade_in);
    if (!S.outro.enabled && m.fade_out && local > tl.D - m.fade_out) fade = Math.min(fade, Math.max(0, (tl.D - local) / m.fade_out));
    frame.style.setProperty('--fade', fade);
    // lớp chữ / ảnh
    $$('#ed-lbox > .ed-l').forEach((el) => {
      const L = S.layers.find((x) => x.id === el.dataset.id); if (!L) { el.remove(); return; }
      el.style.left = L.x * 100 + '%'; el.style.top = L.y * 100 + '%';
      if (L.type === 'text') { el.firstChild.textContent = L.text; styleText(el.firstChild, L); }
      else el.firstChild.style.width = L.size * frame.clientWidth + 'px';
      const end = L.end == null ? tl.D : Math.min(tl.D, L.end);
      const vis = inMain || (local >= L.start && local <= end);
      if (inMain) textAnim(el.firstChild, L.anim, local, L.start, end, L.opacity); else el.firstChild.style.opacity = 0;
      el.classList.toggle('sel', L.id === sel);
      el.classList.toggle('ghost', L.id === sel && (!vis || local < L.start || local > end));
    });
    // thanh tiến độ
    const bar = $('#ed-bar');
    bar.hidden = !S.progress.enabled || !inMain;
    bar.style.background = S.progress.color; bar.style.height = Math.max(2, S.progress.height * FH) + 'px';
    bar.style.width = Math.max(0, Math.min(1, local / tl.D)) * 100 + '%';
    bar.style.top = S.progress.pos === 'top' ? '0' : ''; bar.style.bottom = S.progress.pos === 'top' ? '' : '0';
    drawSeg('intro', tl); drawSeg('outro', tl);
    // nhạc
    if (music.src) {
      music.volume = Math.min(1, S.music.volume);
      if (playing) { const mt = t % (music.duration || 1e9); if (Math.abs(music.currentTime - mt) > 0.4) music.currentTime = mt; if (music.paused) music.play().catch(() => {}); }
      else if (!music.paused) music.pause();
    }
    // giờ + đầu đọc
    $('#ed-time').textContent = `${clock(t)} / ${clock(tl.total)}`;
    $('#tl-ph').style.left = pxOf(t) + 'px';
    if (still) drawTimeline(tl);
  }
  const clock = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, '0')}`;

  // ---------- phát ----------
  function loop(ts) {
    if (!playing) return;
    const dt = lastTs ? (ts - lastTs) / 1000 : 0; lastTs = ts;
    const tl = timeline();
    // trong đoạn video chính: theo đồng hồ của video (không lệch tiếng)
    if (hasVideo && t >= tl.mainStart && t < tl.mainEnd - 0.05 && !V.paused) t = tl.mainStart + (V.currentTime - tl.t0) / S.main.speed;
    else t += dt;
    if (t >= tl.total) { t = tl.total; play(false); }
    draw(false);
    requestAnimationFrame(loop);
  }
  function play(on) {
    playing = on ?? !playing;
    if (playing && t >= timeline().total - 0.05) t = 0;
    root.classList.toggle('playing', playing); lastTs = 0;
    if (!playing) $$('.ed-seg video').forEach((v) => v.pause());
    if (playing) requestAnimationFrame(loop); else draw(false);
  }
  function seek(x) { t = Math.max(0, Math.min(timeline().total, x)); draw(false); }
  $('#ed-play').addEventListener('click', () => play());
  document.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) && document.activeElement.type !== 'range';
    if (typing) return;
    if (e.code === 'Space') { e.preventDefault(); play(); }
    if ((e.key === 'Delete' || e.key === 'Backspace') && sel) { e.preventDefault(); delLayer(sel); }
    if (e.key === 'ArrowLeft') seek(t - (e.shiftKey ? 1 : 0.1));
    if (e.key === 'ArrowRight') seek(t + (e.shiftKey ? 1 : 0.1));
  });

  // ---------- nền intro / outro ----------
  function loadSegMedia() {
    for (const k of ['intro', 'outro']) {
      const el = segEls[k], v = $('video', el), f = S[k].file;
      const url = isVidFile(f) ? `/tpl/asset/${f}` : '';
      if (v.getAttribute('src') !== url) {
        if (url) {
          v.src = url;
          v.addEventListener('loadedmetadata', () => { fileDur[f] = v.duration; refreshUI(); }, { once: true });
        } else { v.removeAttribute('src'); v.load?.(); }
      }
      v.hidden = !url;
    }
  }
  $$('[data-fitdur]').forEach((b) => b.addEventListener('click', () => {
    const k = b.dataset.fitdur, d = fileDur[S[k].file] || $('video', segEls[k]).duration;
    if (!(d > 0)) { toast('Chưa đọc được độ dài video — thử lại sau vài giây', true); return; }
    S[k].duration = Math.min(30, Math.max(0.5, Math.round(d * 10) / 10)); changed();
  }));
  function loadMusic() {
    music.pause();
    if (S.music.file) { music.src = '/tpl/asset/' + S.music.file; music.loop = true; } else music.removeAttribute('src');
  }

  // ---------- lớp chữ / ảnh ----------
  const uid = () => Math.random().toString(16).slice(2, 10);
  const PRESET = {
    title: { type: 'text', text: 'Tiêu đề của bạn', x: 0.5, y: 0.2, size: 0.055, color: '#ffffff', stroke: '#000000', stroke_w: 0.08, anim: 'slide_up', start: 0, end: 4 },
    boxed: { type: 'text', text: 'Chữ có nền', x: 0.5, y: 0.75, size: 0.04, color: '#ffffff', stroke: '#000000', stroke_w: 0, box: true, box_color: '#0f766e', box_opacity: 0.9, anim: 'fade', start: 0.5, end: null },
    watermark: { type: 'text', text: '@tenkenh', x: 0.82, y: 0.06, size: 0.028, color: '#ffffff', stroke: '#000000', stroke_w: 0.06, opacity: 0.8, anim: 'none', start: 0, end: null },
    cta: { type: 'text', text: 'Theo dõi để xem phần tiếp!', x: 0.5, y: 0.82, size: 0.042, color: '#ffe14d', stroke: '#000000', stroke_w: 0.1, anim: 'slide_up', start: -4, end: null },
  };
  async function addLayer(kind) {
    let L;
    if (kind === 'logo') {
      const j = await pickFile('image/*'); if (!j) return;
      L = { type: 'image', file: j.name, x: 0.15, y: 0.08, size: 0.16, opacity: 1, anim: 'none', start: 0, end: null };
    } else L = structuredClone(PRESET[kind]);
    if (L.start < 0) L.start = Math.max(0, timeline().D + L.start);
    L = Object.assign({ id: uid(), opacity: 1, font: 'extrabold', stroke: '#000000', stroke_w: 0.08, box: false, box_color: '#000000', box_opacity: 0.5, anim: 'none', end: null }, L);
    S.layers.push(L); sel = L.id;
    seek(timeline().mainStart + L.start + 0.5);
    changed(); editLayer();
  }
  $$('[data-add]').forEach((b) => b.addEventListener('click', () => addLayer(b.dataset.add)));
  function delLayer(id) {
    S.layers = S.layers.filter((x) => x.id !== id); if (sel === id) sel = null;
    changed(); editLayer();
  }
  $('#le-del').addEventListener('click', () => sel && delLayer(sel));
  function renderLayers() {
    const box = $('#ed-layers');
    box.innerHTML = S.layers.length ? '' : '<p class="small muted">Chưa có chữ / logo — chọn 1 kiểu ở trên.</p>';
    S.layers.forEach((L) => {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'ed-lrow' + (L.id === sel ? ' on' : '');
      b.innerHTML = `<i>${L.type === 'text' ? 'T' : '▣'}</i><span>${esc(L.type === 'text' ? L.text.split('\n')[0] || '(trống)' : fileLabels[L.file] || 'Ảnh')}</span><small>${L.start.toFixed(1)}–${L.end == null ? 'hết' : L.end.toFixed(1)} s</small>`;
      b.addEventListener('click', () => { sel = L.id; editLayer(); refreshUI(); seek(timeline().mainStart + L.start + 0.3); });
      box.appendChild(b);
    });
    // phần tử trên khung xem trước
    const lbox = $('#ed-lbox');
    const have = new Set($$('.ed-l', lbox).map((e) => e.dataset.id));
    S.layers.forEach((L) => {
      let el = lbox.querySelector(`[data-id="${L.id}"]`);
      if (el && el.dataset.type !== L.type + (L.file || '')) { el.remove(); el = null; }
      if (!el) {
        el = document.createElement('div'); el.className = 'ed-l'; el.dataset.id = L.id; el.dataset.type = L.type + (L.file || '');
        el.appendChild(L.type === 'text' ? document.createElement('span') : Object.assign(document.createElement('img'), { src: '/tpl/asset/' + L.file, draggable: false }));
        lbox.appendChild(el); dragLayer(el);
      }
      have.delete(L.id);
    });
    have.forEach((id) => lbox.querySelector(`[data-id="${id}"]`)?.remove());
  }
  function editLayer() {
    const L = S.layers.find((x) => x.id === sel), ed = $('#ed-layer-ed');
    ed.hidden = !L; if (!L) return;
    $('#le-title').textContent = L.type === 'text' ? 'Chữ đang chọn' : 'Ảnh / logo đang chọn';
    $$('[data-lt]', ed).forEach((e) => { e.hidden = e.dataset.lt !== L.type; });
    $$('[data-lk]', ed).forEach((el) => {
      const v = L[el.dataset.lk];
      if (el.type === 'checkbox') el.checked = !!v; else el.value = v == null ? '' : v;
    });
    $$('[data-lo]', ed).forEach((o) => { o.textContent = fmt(L[o.dataset.lo], o.dataset.fmt); });
    $('[data-lbox]', ed).hidden = !L.box;
    const fn = $('[data-lfile] [data-fname]', ed); if (fn) fn.textContent = fileLabels[L.file] || '';
  }
  $$('#ed-layer-ed [data-lk]').forEach((el) => {
    const ev = el.type === 'checkbox' || el.tagName === 'SELECT' ? 'change' : 'input';
    el.addEventListener(ev, () => {
      const L = S.layers.find((x) => x.id === sel); if (!L) return;
      const k = el.dataset.lk;
      let v = el.type === 'checkbox' ? el.checked : el.value;
      if (el.type === 'range' || el.type === 'number') v = el.value === '' ? null : Number(el.value);
      if (k === 'start' && v == null) v = 0;
      L[k] = v;
      $$('#ed-layer-ed [data-lo]').forEach((o) => { o.textContent = fmt(L[o.dataset.lo], o.dataset.fmt); });
      $('#ed-layer-ed [data-lbox]').hidden = !L.box;
      changed();
    });
  });
  $('#ed-layer-ed [data-lfile] [data-pick]').addEventListener('click', async () => {
    const L = S.layers.find((x) => x.id === sel); if (!L) return;
    const j = await pickFile('image/*'); if (!j) return; L.file = j.name; changed(); editLayer();
  });
  $('#le-pos').addEventListener('click', (e) => {
    const b = e.target.closest('[data-x]'), L = S.layers.find((x) => x.id === sel); if (!b || !L) return;
    L.x = Number(b.dataset.x); L.y = Number(b.dataset.y); changed();
  });
  function dragLayer(el) {
    el.addEventListener('pointerdown', (e) => {
      const L = S.layers.find((x) => x.id === el.dataset.id); if (!L) return;
      e.preventDefault(); sel = L.id; openTab('text'); editLayer(); refreshUI();
      const r = frame.getBoundingClientRect(), ox = e.clientX - L.x * r.width, oy = e.clientY - L.y * r.height;
      el.setPointerCapture(e.pointerId); el.classList.add('dragging');
      const mv = (ev) => {
        let x = (ev.clientX - ox) / r.width, y = (ev.clientY - oy) / r.height;
        const sx = Math.abs(x - 0.5) < 0.015, sy = Math.abs(y - 0.5) < 0.015;     // hít vào giữa
        if (sx) x = 0.5; if (sy) y = 0.5;
        $('#ed-gv').hidden = !sx; $('#ed-gh').hidden = !sy;
        L.x = Math.max(0, Math.min(1, x)); L.y = Math.max(0, Math.min(1, y)); draw(false);
      };
      const up = () => {
        el.removeEventListener('pointermove', mv); el.classList.remove('dragging');
        $('#ed-gv').hidden = $('#ed-gh').hidden = true; changed();
      };
      el.addEventListener('pointermove', mv); el.addEventListener('pointerup', up, { once: true });
    });
  }
  frame.addEventListener('pointerdown', (e) => { if (!e.target.closest('.ed-l') && sel) { sel = null; editLayer(); refreshUI(); draw(true); } });

  // ---------- dòng thời gian ----------
  const tlBody = $('#ed-tl');
  let pxPerS = 40;
  const pxOf = (s) => 8 + s * pxPerS;
  function drawTimeline(tl) {
    const w = tlBody.clientWidth - 24;
    pxPerS = Math.max(8, w / Math.max(1, tl.total));
    const ruler = $('#tl-ruler'); ruler.innerHTML = '';
    const step = pxPerS > 60 ? 1 : pxPerS > 25 ? 2 : 5;
    for (let s = 0; s <= tl.total + 0.001; s += step) {
      const i = document.createElement('span'); i.style.left = pxOf(s) + 'px'; i.textContent = clock(s).replace(/\.\d$/, ''); ruler.appendChild(i);
    }
    const vt = $('#tl-video'); vt.innerHTML = '';
    const blk = (cls, a, b, label, tab) => {
      const d = document.createElement('div'); d.className = 'tl-b ' + cls; d.style.left = pxOf(a) + 'px'; d.style.width = Math.max(4, (b - a) * pxPerS) + 'px';
      d.innerHTML = `<span>${esc(label)}</span>`; if (tab) d.addEventListener('click', (e) => { e.stopPropagation(); openTab(tab); });
      return d;
    };
    if (S.intro.enabled) vt.appendChild(blk('b-intro', 0, S.intro.duration, 'Intro', 'intro'));
    const mb = blk('b-main', tl.mainStart, tl.mainEnd, 'Video chính · ' + tl.D.toFixed(1) + ' s', 'canvas');
    if (thumbURL) mb.style.backgroundImage = `url(${thumbURL})`;
    vt.appendChild(mb);
    if (S.outro.enabled) vt.appendChild(blk('b-outro', tl.outStart, tl.total, 'Outro', 'outro'));
    for (const [k, at, td] of [['intro', tl.mainStart, tl.tdIn], ['outro', tl.outStart, tl.tdOut]]) {
      if (!S[k].enabled) continue;
      const d = document.createElement('button'); d.type = 'button'; d.className = 'tl-x' + (td ? '' : ' cut');
      d.style.left = pxOf(at + td / 2) + 'px'; d.title = 'Chuyển cảnh: ' + (ED.trans[S[k].transition] || '');
      d.addEventListener('click', (e) => { e.stopPropagation(); openTab(k); $(`[data-trans="${k}"]`).scrollIntoView({ block: 'center' }); });
      vt.appendChild(d);
    }
    const lt = $('#tl-layers'); lt.innerHTML = '';
    lt.style.height = Math.max(1, S.layers.length) * 26 + 6 + 'px';
    $('#tl-h-layers').style.height = lt.style.height;
    S.layers.forEach((L, i) => {
      const end = L.end == null ? tl.D : Math.min(tl.D, L.end);
      const d = blk('b-layer' + (L.type === 'image' ? ' img' : '') + (L.id === sel ? ' on' : ''), tl.mainStart + L.start, tl.mainStart + end,
        L.type === 'text' ? L.text.split('\n')[0] : (fileLabels[L.file] || 'Logo'));
      d.style.top = 3 + i * 26 + 'px';
      d.insertAdjacentHTML('beforeend', '<i class="h l"></i><i class="h r"></i>');
      tlDrag(d, L, tl);
      lt.appendChild(d);
    });
    const mu = $('#tl-music'); mu.innerHTML = '';
    if (S.music.file) mu.appendChild(blk('b-music', 0, tl.total, '♪ ' + (fileLabels[S.music.file] || 'Nhạc nền'), 'music'));
  }
  function tlDrag(d, L, tl) {
    d.addEventListener('pointerdown', (e) => {
      e.stopPropagation(); sel = L.id; openTab('text'); editLayer(); refreshUI();
      const mode = e.target.classList.contains('l') ? 'l' : e.target.classList.contains('r') ? 'r' : 'm';
      const x0 = e.clientX, s0 = L.start, e0 = L.end == null ? tl.D : L.end;
      d.setPointerCapture(e.pointerId);
      const mv = (ev) => {
        const ds = Math.round(((ev.clientX - x0) / pxPerS) * 10) / 10;
        if (mode === 'm') { const len = e0 - s0; L.start = Math.max(0, Math.min(tl.D - 0.3, s0 + ds)); L.end = Math.min(tl.D, L.start + len); }
        if (mode === 'l') L.start = Math.max(0, Math.min(e0 - 0.3, s0 + ds));
        if (mode === 'r') L.end = Math.max(L.start + 0.3, Math.min(tl.D, e0 + ds));
        if (L.end != null && L.end >= tl.D - 0.05) L.end = null;
        d.style.left = pxOf(tl.mainStart + L.start) + 'px';
        d.style.width = ((L.end == null ? tl.D : L.end) - L.start) * pxPerS + 'px';
        seek(tl.mainStart + (mode === 'r' ? (L.end ?? tl.D) - 0.05 : L.start + 0.05)); editLayer();
      };
      d.addEventListener('pointermove', mv);
      d.addEventListener('pointerup', () => { d.removeEventListener('pointermove', mv); changed(); }, { once: true });
    });
  }
  tlBody.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.b-layer, .tl-x')) return;
    const r = tlBody.getBoundingClientRect();
    const go = (ev) => seek((ev.clientX - r.left + tlBody.scrollLeft - 8) / pxPerS);
    go(e); tlBody.setPointerCapture(e.pointerId);
    tlBody.addEventListener('pointermove', go);
    tlBody.addEventListener('pointerup', () => tlBody.removeEventListener('pointermove', go), { once: true });
  });

  // ---------- ảnh nhỏ cho bộ lọc + dải video ----------
  let thumbURL = '';
  function grabThumb() {
    const go = () => {
      try {
        const c = document.createElement('canvas'), k = 120 / Math.max(vw, vh);
        c.width = Math.round(vw * k); c.height = Math.round(vh * k);
        c.getContext('2d').drawImage(V, 0, 0, c.width, c.height);
        thumbURL = c.toDataURL('image/jpeg', 0.7);
        renderFilters(); drawTimeline(timeline());
      } catch (e) { /* video chưa có khung */ }
    };
    if (V.readyState >= 2) go(); else V.addEventListener('loadeddata', go, { once: true });
  }
  const FILTERS = [
    ['Gốc', { brightness: 0, contrast: 1, saturation: 1, temperature: 6500, hue: 0, grain: 0, vignette: false, sharpen: 0 }],
    ['Sáng rực', { brightness: 0.05, contrast: 1.08, saturation: 1.25, temperature: 6500, hue: 0, grain: 0, vignette: false, sharpen: 0.3 }],
    ['Điện ảnh', { brightness: -0.03, contrast: 1.18, saturation: 0.85, temperature: 6900, hue: 0, grain: 4, vignette: true, sharpen: 0.2 }],
    ['Ấm áp', { brightness: 0.02, contrast: 1.05, saturation: 1.1, temperature: 4800, hue: 0, grain: 0, vignette: false, sharpen: 0 }],
    ['Mát lạnh', { brightness: 0.01, contrast: 1.05, saturation: 0.95, temperature: 8300, hue: 0, grain: 0, vignette: false, sharpen: 0 }],
    ['Cổ điển', { brightness: 0.03, contrast: 0.9, saturation: 0.65, temperature: 5200, hue: 0, grain: 14, vignette: true, sharpen: 0 }],
    ['Đen trắng', { brightness: 0, contrast: 1.2, saturation: 0, temperature: 6500, hue: 0, grain: 6, vignette: false, sharpen: 0.2 }],
    ['Rực rỡ', { brightness: 0, contrast: 1.12, saturation: 1.65, temperature: 6500, hue: 0, grain: 0, vignette: false, sharpen: 0.4 }],
    ['Mơ màng', { brightness: 0.07, contrast: 0.88, saturation: 0.9, temperature: 6000, hue: -8, grain: 0, vignette: false, sharpen: 0 }],
  ];
  function renderFilters() {
    const box = $('#ed-filters'); box.innerHTML = '';
    FILTERS.forEach(([name, f]) => {
      const on = Object.entries(f).every(([k, v]) => S.main[k] === v);
      const b = document.createElement('button'); b.type = 'button'; b.setAttribute('aria-pressed', String(on));
      const css = `brightness(${1 + f.brightness * 1.6}) contrast(${f.contrast}) saturate(${f.saturation}) hue-rotate(${f.hue}deg)` +
        (f.temperature < 6500 ? ' sepia(0.25)' : '');
      b.innerHTML = `<span class="fi" style="${thumbURL ? `background-image:url(${thumbURL});` : ''}filter:${css}"></span><span>${name}</span>`;
      b.addEventListener('click', () => { Object.assign(S.main, f); changed(); renderFilters(); });
      box.appendChild(b);
    });
  }
  $('#ed-adj-reset').addEventListener('click', () => { Object.assign(S.main, FILTERS[0][1], { zoom: 0, fade_in: 0, fade_out: 0 }); changed(); renderFilters(); });
  $('#ed-reset').addEventListener('click', () => {
    if (!confirm('Đưa mẫu edit của kênh về mặc định? Intro, outro, chữ, logo, nhạc và hiệu ứng bị xoá.')) return;
    const base = S.base; Object.keys(S).forEach((k) => delete S[k]); Object.assign(S, structuredClone(ED.default), { base });
    sel = null; editLayer(); loadSegMedia(); loadMusic(); changed(); layout(); renderFilters();
  });

  // ---------- chép sang kênh ----------
  $('#ed-copy-f')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearTimeout(saveT); await save();
    const r = await fetch(`/pages/${ED.pid}/tpl/copy`, { method: 'POST', body: new FormData(e.target) });
    toast(await r.text(), !r.ok); e.target.closest('details').open = false;
  });

  // ---------- render thử ----------
  const dlg = $('#ed-rdlg'), rbody = $('#rd-body');
  $('#rd-x').addEventListener('click', () => dlg.close());
  $('#ed-render').addEventListener('click', () => renderTest(false));
  async function renderTest(full) {
    const sample = sampleSel.value;
    play(false);
    dlg.showModal();
    rbody.innerHTML = '<p class="rd-wait"><i class="spin"></i> Đang render bằng FFmpeg… (video ngắn mất khoảng 10–60 giây)</p>';
    const { base, ...tpl } = S;
    try {
      const r = await fetch('/tpl/render', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template: tpl, sample, full, base }) });
      if (!r.ok) throw new Error(await r.text());
      const { key } = await r.json();
      for (;;) {
        await new Promise((res) => setTimeout(res, 1000));
        if (!dlg.open) return;
        const j = await (await fetch('/tpl/render/' + key)).json();
        if (j.state === 'running') { rbody.querySelector('.rd-wait').lastChild.textContent = ` Đang render bằng FFmpeg… ${Math.round(j.elapsed)} s`; continue; }
        if (j.state === 'error') throw new Error(j.error);
        rbody.innerHTML = `<video src="${j.url}" controls autoplay playsinline></video>
          <p class="small muted">Bản thật (${full ? 'đủ cỡ' : 'cỡ nhỏ'}) · dài ${j.dur.toFixed(1)} s · render mất ${Math.round(j.took)} s.
          Bản này chưa có phụ đề / lồng tiếng mới — video mẫu là bản đã edit sẵn của kênh. ${full ? '' : '<button type="button" class="btn sm" id="rd-full">Render đủ cỡ</button>'}</p>`;
        $('#rd-full')?.addEventListener('click', () => renderTest(true));
        return;
      }
    } catch (e) {
      rbody.innerHTML = `<p class="rd-err">Không render được: ${esc(e.message)}</p>`;
    }
  }

  // ---------- khởi động ----------
  bindAll(); refreshUI(); renderFilters(); loadSegMedia(); loadMusic(); loadSample(); layout();
  if (window.pywebview || /in-app/.test(document.documentElement.className)) document.documentElement.classList.add('in-app');
})();

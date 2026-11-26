/* Cửa sổ Mẫu edit của kênh — kiểu CapCut.
   State S = mẫu (vtemplate) + S.base (lật / màu phủ / nghiêng). Ô có data-k="đường.dẫn" tự gắn 2 chiều (cả trong thư viện
   lẫn bảng thuộc tính). Thư viện -> kéo thả (HTML5 DnD) vào khung xem / dòng thời gian; thả file từ máy = tải lên.
   Xem trước = CSS gần đúng; Render thử = FFmpeg thật. Tự lưu 0,5 s sau lần sửa cuối, Ctrl+Z / Ctrl+Y hoàn tác. */
(() => {
  'use strict';
  const ED = window.__ED, $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  let S = structuredClone(ED.tpl); S.base = structuredClone(ED.base);
  const root = $('#ed'), frame = $('#ed-frame'), stage = $('#ed-stage'), V = $('#ed-v'), BG = $('#ed-bgv');
  const ibody = $('#ed-ibody'), ihead = $('#ed-ihead'), libbody = $('#ed-libbody');
  let sel = { k: 'main' };             // main | intro | outro | tin | tout | layer (id) | music
  const itab = { main: 'basic', intro: 'content', outro: 'content' };
  let libTab = 'media';
  let srcDur = 10, vw = 1080, vh = 1920, hasVideo = false;
  let t = 0, playing = false, lastTs = 0;
  const music = new Audio();
  let assets = [];
  const fileDur = {};
  const isVidFile = (f) => !!f && /\.(mp4|mov|webm)$/.test(f), isImgFile = (f) => !!f && /\.(png|jpe?g|webp)$/.test(f);

  // ---------- tiện ích ----------
  const get = (p) => p.split('.').reduce((o, k) => (o == null ? o : o[k]), S);
  const set = (p, v) => { const ks = p.split('.'), last = ks.pop(); ks.reduce((o, k) => o[k], S)[last] = v; };
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const fmt = (v, f) => {
    v = Number(v);
    switch (f) {
      case 's': return v.toFixed(1).replace('.', ',') + ' s';
      case 'x': return v.toFixed(2).replace('.', ',') + '×';
      case 'pct': return Math.round(v * 100) + '%';
      case 'deg': return (v > 0 ? '+' : '') + v + '°';
      case 'K': return v + 'K';
      case 'signed': return (v > 0 ? '+' : '') + Math.round(v * 100);
      default: return String(Math.round(v * 100) / 100).replace('.', ',');
    }
  };
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const clock = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, '0')}`;
  const toast = (msg, err) => {
    const el = $('#ed-toast'); el.textContent = msg; el.className = 'ed-toast' + (err ? ' err' : ''); el.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => { el.hidden = true; }, 3500);
  };
  const asset = (n) => assets.find((a) => a.name === n);
  const label = (n) => asset(n)?.label || 'File';
  const uid = () => Math.random().toString(16).slice(2, 10);
  const layerIdx = (id) => S.layers.findIndex((x) => x.id === id);
  const curLayer = () => (sel.k === 'layer' ? S.layers.find((x) => x.id === sel.id) : null);

  // ---------- lưu + hoàn tác ----------
  let saveT = null, saving = false, again = false;
  let lastSnap = JSON.stringify(S), hist = [], redoS = [];
  const saveEl = $('#ed-save');
  function pushHist() {
    const cur = JSON.stringify(S);
    if (cur !== lastSnap) { hist.push(lastSnap); if (hist.length > 100) hist.shift(); redoS = []; lastSnap = cur; }
    $('#ed-undo').disabled = !hist.length; $('#ed-redo').disabled = !redoS.length;
  }
  function changed(o = {}) {
    saveEl.textContent = 'Chưa lưu'; saveEl.className = 'ed-save wait';
    clearTimeout(saveT); saveT = setTimeout(save, 500);
    if (o.insp) renderInspector(); else refreshOutputs();
    if (o.lib) renderLib();
    syncLayerEls(); renderTimeline(); draw();
  }
  async function save() {
    pushHist();
    if (saving) { again = true; return; }
    saving = true; saveEl.textContent = 'Đang lưu…';
    const { base, ...tpl } = S;
    try {
      const r = await fetch(`/pages/${ED.pid}/tpl`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tpl, base }) });
      if (!r.ok) throw new Error(await r.text());
      saveEl.textContent = 'Đã lưu vào kênh'; saveEl.className = 'ed-save';
    } catch (e) { saveEl.textContent = 'Lỗi lưu'; saveEl.className = 'ed-save err'; toast('Không lưu được: ' + e.message, true); }
    saving = false;
    if (again) { again = false; save(); }
  }
  function replaceState(snap) {
    S = JSON.parse(snap); lastSnap = snap;
    if (sel.k === 'layer' && layerIdx(sel.id) < 0) sel = { k: 'main' };
    loadSegMedia(); loadMusic(); layout(); renderAll();
    $('#ed-undo').disabled = !hist.length; $('#ed-redo').disabled = !redoS.length;
    saveEl.textContent = 'Chưa lưu'; saveEl.className = 'ed-save wait';
    clearTimeout(saveT); saveT = setTimeout(save, 300);
  }
  function undo() { clearTimeout(saveT); pushHist(); if (!hist.length) return; redoS.push(lastSnap); replaceState(hist.pop()); }
  function redo() { pushHist(); if (!redoS.length) return; hist.push(lastSnap); replaceState(redoS.pop()); }
  $('#ed-undo').addEventListener('click', undo); $('#ed-redo').addEventListener('click', redo);
  window.addEventListener('beforeunload', () => { if (saveEl.classList.contains('wait')) save(); });

  // ---------- gắn ô <-> state (uỷ quyền cho cả cửa sổ) ----------
  const isText = (el) => el.tagName === 'TEXTAREA' || ['text', 'number'].includes(el.type);
  function readVal(el) {
    if (el.type === 'checkbox') return el.checked;
    if (el.type === 'range' || el.type === 'number') return el.value === '' && el.dataset.null ? null : Number(el.value || 0);
    return el.value;
  }
  root.addEventListener('input', (e) => {
    const el = e.target.closest('[data-k]'); if (!el || el.type === 'checkbox' || el.tagName === 'SELECT') return;
    set(el.dataset.k, readVal(el)); changed();
  });
  root.addEventListener('change', (e) => {
    const el = e.target.closest('[data-k]'); if (!el) return;
    set(el.dataset.k, readVal(el));
    if (el.dataset.k.startsWith('canvas.')) layout();
    changed({ insp: !isText(el) && el.type !== 'range' && el.type !== 'color', lib: libTab === 'adjust' && el.type === 'checkbox' });
  });
  root.addEventListener('click', (e) => {
    const c = e.target.closest('[data-choice] [data-v]');
    if (c) {
      const p = c.closest('[data-choice]').dataset.choice; set(p, c.dataset.v);
      if (p.startsWith('canvas.')) layout();
      changed({ insp: true }); return;
    }
    const a = e.target.closest('[data-act]'); if (a) { e.stopPropagation(); act(a.dataset.act, a); }
  });
  function refreshOutputs() {
    $$('[data-o]').forEach((o) => { const v = get(o.dataset.o); if (v != null) o.textContent = fmt(v, o.dataset.fmt); });
    $$('[data-k]').forEach((el) => {
      if (el === document.activeElement) return;
      const v = get(el.dataset.k);
      if (el.type === 'checkbox') el.checked = !!v; else el.value = v == null ? '' : v;
    });
    $('#ed-ratio').value = S.canvas.ratio;
  }

  // ---------- mẫu HTML cho ô ----------
  const H = {
    rng: (p, lb, a, b, st, f = '') => `<div class="ed-rng"><label for="i-${p}">${lb}</label><input id="i-${p}" type="range" min="${a}" max="${b}" step="${st}" data-k="${p}" value="${get(p)}"><output data-o="${p}" data-fmt="${f}">${fmt(get(p), f)}</output></div>`,
    color: (p, lb) => `<label class="ed-col">${lb}<input type="color" data-k="${p}" value="${get(p)}"></label>`,
    sw: (p, lb) => `<label class="ed-sw"><input type="checkbox" data-k="${p}" ${get(p) ? 'checked' : ''}><span></span>${lb}</label>`,
    sel: (p, lb, o) => `<div class="ed-f"><label>${lb}</label><select data-k="${p}">${Object.entries(o).map(([k, v]) => `<option value="${k}" ${String(get(p)) === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select></div>`,
    chips: (p, lb, o) => `<div class="ed-f">${lb ? `<span class="lb">${lb}</span>` : ''}<div class="ed-chips" data-choice="${p}">${Object.entries(o).map(([k, v]) => `<button type="button" data-v="${k}" aria-pressed="${String(get(p)) === k}">${esc(v)}</button>`).join('')}</div></div>`,
    text: (p, lb, rows, ph = '') => `<div class="ed-f"><label>${lb}</label>${rows ? `<textarea rows="${rows}" data-k="${p}" placeholder="${esc(ph)}">${esc(get(p))}</textarea>` : `<input type="text" autocomplete="off" data-k="${p}" value="${esc(get(p))}" placeholder="${esc(ph)}">`}</div>`,
    num: (p, lb, ph = '') => `<label class="ed-col">${lb}<input type="number" min="0" step="0.1" data-k="${p}" data-null="1" value="${get(p) ?? ''}" placeholder="${esc(ph)}"></label>`,
    file: (p, accept, empty) => {
      const f = get(p), th = f && (isImgFile(f) || isVidFile(f)) ? `style="background-image:url(/tpl/thumb/${f})"` : '';
      const d = f ? fileDur[f] || asset(f)?.dur : 0;
      return `<div class="ed-file"><span class="ed-fthumb" ${th}>${f ? (th ? '' : '♪') : 'trống'}</span>
        <span class="ed-fname">${f ? esc(label(f)) + (d ? ` · ${fmt(d, 's')}` : '') : esc(empty)}</span>
        <button type="button" class="ed-b" data-act="pick:${p}" data-accept="${accept}">${f ? 'Đổi' : 'Chọn…'}</button>
        ${f ? `<button type="button" class="ed-b" data-act="clear:${p}">Bỏ</button>` : ''}</div>`;
    },
    tabs: (k, tabs) => tabs.map(([v, lb]) => `<button type="button" class="ed-itab" data-act="itab:${k}:${v}" aria-selected="${itab[k] === v}">${lb}</button>`).join(''),
    trgrid: (k) => `<div class="ed-trgrid">${Object.entries(ED.trans).map(([v, lb]) => `<button type="button" data-act="trans:${k}:${v}" aria-pressed="${S[k].transition === v}"><i class="tr-ico tr-${v}"></i>${esc(lb)}</button>`).join('')}</div>`,
  };

  // ---------- bảng thuộc tính ----------
  function renderInspector() {
    let h = '', b = '';
    frame.classList.toggle('sel-main', sel.k === 'main');
    if (sel.k === 'main') {
      h = H.tabs('main', [['basic', 'Video'], ['adjust', 'Điều chỉnh'], ['frame', 'Khung hình']]);
      if (itab.main === 'basic') b = `
        ${H.rng('main.speed', 'Tốc độ', 0.5, 2, 0.05, 'x')}
        ${H.rng('main.trim_start', 'Cắt bỏ đầu video', 0, Math.max(1, Math.floor(srcDur / 2)), 0.1, 's')}
        ${H.rng('main.trim_end', 'Cắt bỏ cuối video', 0, Math.max(1, Math.floor(srcDur / 2)), 0.1, 's')}
        ${H.rng('main.volume', 'Âm lượng giọng / tiếng gốc', 0, 2, 0.05, 'pct')}
        <h4>Chỉnh của kênh (làm ở bước edit chính)</h4>
        ${H.sw('base.hflip', 'Lật ngang')}
        ${H.rng('base.rotate_deg', 'Nghiêng', -5, 5, 0.5, 'deg')}
        <div class="ed-row">${H.color('base.tint_color', 'Màu phủ')}<div class="grow">${H.rng('base.tint_opacity', 'Độ đậm phủ', 0, 0.4, 0.01, 'pct')}</div></div>
        <p class="ed-ihint">Video xem thử đã edit sẵn nên đã có 3 chỉnh này — khung xem chỉ thể hiện khi bạn chọn "Tải video lên…" (video gốc).
        Cắt đầu / cuối cũng kéo được ở mép khối "Video chính" dưới dòng thời gian.</p>`;
      else if (itab.main === 'adjust') b = adjustHTML();
      else b = `${H.chips('canvas.ratio', 'Tỉ lệ khung', ED.ratios)}
        ${H.chips('canvas.fit', 'Video không vừa khung', { blur: 'Nền mờ', black: 'Nền đen', crop: 'Cắt cho đầy' })}
        <h4>Thanh tiến độ</h4>${H.sw('progress.enabled', 'Hiện thanh chạy theo video')}
        ${S.progress.enabled ? `<div class="ed-row">${H.color('progress.color', 'Màu')}<div class="grow">${H.chips('progress.pos', 'Vị trí', { top: 'Trên', bottom: 'Dưới' })}</div></div>` : ''}`;
    } else if (sel.k === 'intro' || sel.k === 'outro') {
      const k = sel.k, s = S[k];
      h = `<b>${k === 'intro' ? 'Intro' : 'Outro'}</b>` + H.tabs(k, [['content', 'Nội dung'], ['trans', 'Chuyển cảnh']]);
      if (itab[k] === 'trans') b = transHTML(k);
      else b = `
        <h4>Video / ảnh nền</h4>${H.file(k + '.file', 'video/*,image/*', 'Nền màu — kéo video / ảnh từ thư viện vào ô ' + (k === 'intro' ? 'Intro' : 'Outro'))}
        ${isVidFile(s.file) ? `${H.sw(k + '.audio', 'Giữ tiếng của video')}${s.audio ? H.rng(k + '.volume', 'Âm lượng video', 0, 2, 0.05, 'pct') : ''}
          <button type="button" class="ed-b" style="margin-top:10px" data-act="fitdur:${k}">Độ dài = hết video</button>` : ''}
        ${s.file ? H.chips(k + '.fit', 'Không vừa khung', { crop: 'Cắt cho đầy', blur: 'Vừa, nền mờ', pad: 'Vừa, nền màu' }) : ''}
        ${H.rng(k + '.duration', 'Độ dài', 0.5, 30, 0.1, 's')}
        ${H.color(k + '.bg', 'Màu nền')}
        <h4>Chữ</h4>
        ${H.text(k + '.title', 'Tiêu đề', 2, 'Để trống nếu video đã có chữ')}
        ${H.text(k + '.sub', 'Dòng phụ (có nền màu nhấn)', 0)}
        <div class="ed-row">${H.color(k + '.color', 'Màu chữ')}${H.color(k + '.accent', 'Màu nhấn')}<div class="grow">${H.sel(k + '.anim', 'Hiệu ứng', ED.anims)}</div></div>
        ${H.rng(k + '.size', 'Cỡ tiêu đề', 0.02, 0.15, 0.005, 'pct')}
        <button type="button" class="ed-b danger ed-del" data-act="del">Xoá ${k === 'intro' ? 'intro' : 'outro'}</button>`;
    } else if (sel.k === 'tin' || sel.k === 'tout') {
      const k = sel.k === 'tin' ? 'intro' : 'outro';
      h = `<b>Chuyển cảnh ${k === 'intro' ? 'Intro → Video' : 'Video → Outro'}</b>`;
      b = transHTML(k);
    } else if (sel.k === 'layer') {
      const L = curLayer(), i = layerIdx(sel.id), p = `layers.${i}`;
      if (!L) { sel = { k: 'main' }; renderInspector(); return; }
      h = `<b>${L.type === 'text' ? 'Văn bản' : 'Ảnh / logo'}</b>`;
      b = (L.type === 'text' ? `
        ${H.text(p + '.text', 'Nội dung', 3)}
        ${H.sel(p + '.font', 'Font', { extrabold: 'Be Vietnam Pro ExtraBold', bold: 'Be Vietnam Pro Bold' })}
        <div class="ed-row">${H.color(p + '.color', 'Màu chữ')}${H.color(p + '.stroke', 'Màu viền')}<div class="grow">${H.rng(p + '.stroke_w', 'Viền', 0, 0.2, 0.01, 'pct')}</div></div>
        ${H.sw(p + '.box', 'Hộp nền sau chữ')}
        ${L.box ? `<div class="ed-row">${H.color(p + '.box_color', 'Màu hộp')}<div class="grow">${H.rng(p + '.box_opacity', 'Độ đậm hộp', 0, 1, 0.05, 'pct')}</div></div>` : ''}`
        : H.file(p + '.file', 'image/*', 'Ảnh')) + `
        ${H.rng(p + '.size', 'Cỡ', 0.01, L.type === 'text' ? 0.15 : 0.8, 0.005, 'pct')}
        ${H.rng(p + '.opacity', 'Độ rõ', 0.1, 1, 0.05, 'pct')}
        <div class="ed-f"><span class="lb">Vị trí nhanh <span class="ed-dim">(hoặc kéo trên khung xem; ô trắng ở góc = đổi cỡ)</span></span>
          <div class="ed-pos">${[0.08, 0.5, 0.9].map((y) => [0.18, 0.5, 0.82].map((x) => `<button type="button" data-act="pos:${x}:${y}" aria-label="Vị trí"></button>`).join('')).join('')}</div></div>
        <h4>Thời gian & hoạt ảnh</h4>
        <div class="ed-row">${H.num(p + '.start', 'Hiện từ (s)')}${H.num(p + '.end', 'Đến (s)', 'hết')}</div>
        ${H.chips(p + '.anim', 'Hoạt ảnh', ED.anims)}
        <p class="ed-ihint">Thời gian tính từ đầu video chính. Kéo / kéo mép thanh của lớp ở dòng thời gian để đổi nhanh.</p>
        <div class="ed-row" style="margin-top:14px"><button type="button" class="ed-b" data-act="dup">Nhân đôi</button><button type="button" class="ed-b danger" data-act="del">Xoá lớp</button></div>`;
    } else if (sel.k === 'music') {
      h = '<b>Nhạc nền</b>';
      b = `${H.file('music.file', 'audio/*,video/mp4', 'Chưa có nhạc — kéo nhạc từ thư viện Âm thanh xuống dòng Nhạc')}
        ${H.rng('music.volume', 'Âm lượng nhạc', 0, 1.5, 0.05, 'pct')}
        ${H.rng('music.fade_out', 'Nhỏ dần lúc cuối', 0, 5, 0.5, 's')}
        ${H.rng('main.volume', 'Âm lượng giọng / tiếng video', 0, 2, 0.05, 'pct')}
        <p class="ed-ihint">Nhạc lặp cho đủ độ dài, trộn dưới giọng lồng tiếng. Dùng nhạc bạn có quyền dùng — Facebook / TikTok có thể tắt tiếng video dùng nhạc bản quyền.</p>
        ${S.music.file ? '<button type="button" class="ed-b danger ed-del" data-act="del">Bỏ nhạc</button>' : ''}`;
    }
    ihead.innerHTML = h; ibody.innerHTML = b;
  }
  function adjustHTML() {
    return `${H.rng('main.brightness', 'Độ sáng', -0.3, 0.3, 0.01, 'signed')}
      ${H.rng('main.contrast', 'Tương phản', 0.5, 1.8, 0.01, 'x')}
      ${H.rng('main.saturation', 'Bão hoà', 0, 2.5, 0.01, 'x')}
      ${H.rng('main.temperature', 'Nhiệt độ màu (thấp = ấm)', 3000, 10000, 100, 'K')}
      ${H.rng('main.hue', 'Đổi sắc', -180, 180, 1, 'deg')}
      ${H.rng('main.sharpen', 'Làm nét', 0, 1.5, 0.05)}
      ${H.rng('main.grain', 'Hạt phim', 0, 40, 1)}
      ${H.sw('main.vignette', 'Tối 4 góc (vignette)')}
      <h4>Chuyển động</h4>
      ${H.rng('main.zoom', 'Phóng to chậm dần', 0, 0.3, 0.01, 'pct')}
      ${H.rng('main.fade_in', 'Hiện dần lúc đầu (khi không có intro)', 0, 3, 0.1, 's')}
      ${H.rng('main.fade_out', 'Tối dần lúc cuối (khi không có outro)', 0, 3, 0.1, 's')}
      <button type="button" class="ed-b" style="margin-top:14px" data-act="adj-reset">Về mặc định</button>`;
  }
  function transHTML(k) {
    if (!S[k].enabled) return `<p class="ed-iempty">Chưa có ${k}. Kéo video / ảnh / thẻ màu vào ô "+ ${k === 'intro' ? 'Intro' : 'Outro'}" ở dòng thời gian.</p>`;
    return `${H.trgrid(k)}${S[k].transition === 'none' ? '' : H.rng(k + '.trans_dur', 'Thời lượng', 0.1, 2, 0.1, 's')}
      <p class="ed-ihint">Có thể kéo 1 kiểu chuyển cảnh từ thư viện thả lên hình thoi ở dòng thời gian.</p>`;
  }

  // ---------- hành động (nút) ----------
  async function act(a, el) {
    const [cmd, x, y] = a.split(':');
    if (cmd === 'itab') { itab[x] = y; renderInspector(); return; }
    if (cmd === 'lib') { openLib(x); return; }
    if (cmd === 'pick') {
      const j = (await pickFiles(el.dataset.accept, false))[0]; if (!j) return;
      if (x === 'intro.file' || x === 'outro.file') { setSeg(x.split('.')[0], j); return; }
      if (x === 'music.file') { setMusic(j.name); return; }
      set(x, j.name); changed({ insp: true }); return;
    }
    if (cmd === 'clear') { set(x, ''); if (x === 'music.file') loadMusic(); else loadSegMedia(); changed({ insp: true }); return; }
    if (cmd === 'fitdur') {
      const d = fileDur[S[x].file] || asset(S[x].file)?.dur || $('video', segEls[x]).duration;
      if (!(d > 0)) { toast('Chưa đọc được độ dài video', true); return; }
      S[x].duration = clamp(Math.round(d * 10) / 10, 0.5, 30); changed({ insp: true }); return;
    }
    if (cmd === 'trans') { setTrans(x, y); return; }
    if (cmd === 'pos') { const L = curLayer(); if (L) { L.x = Number(x); L.y = Number(y); changed(); } return; }
    if (cmd === 'adj-reset') { Object.assign(S.main, FILTERS[0].v, { zoom: 0, fade_in: 0, fade_out: 0 }); changed({ insp: true, lib: true }); return; }
    if (cmd === 'del') { delSel(); return; }
    if (cmd === 'dup') { dupLayer(); return; }
    if (cmd === 'hide') { await fetch(`/tpl/assets/${x}/hide`, { method: 'POST' }); await loadAssets(); return; }
    if (cmd === 'add') { quickAdd(JSON.parse(el.closest('[data-drag]').dataset.drag)); return; }
    if (cmd === 'enable') { setSeg(x, { card: true }); return; }
    if (cmd === 'upload') { await pickFiles(el.dataset.accept || 'video/*,image/*,audio/*', true); }
  }
  function delSel() {
    if (sel.k === 'layer') { S.layers.splice(layerIdx(sel.id), 1); sel = { k: 'main' }; }
    else if (sel.k === 'intro' || sel.k === 'outro') { S[sel.k].enabled = false; sel = { k: 'main' }; }
    else if (sel.k === 'music') { S.music.file = ''; loadMusic(); sel = { k: 'main' }; }
    else { toast('Chọn 1 lớp chữ / logo, intro, outro hoặc nhạc để xoá'); return; }
    changed({ insp: true });
  }
  function dupLayer() {
    const L = curLayer(); if (!L) { toast('Chọn 1 lớp chữ / logo để nhân đôi'); return; }
    const c = { ...structuredClone(L), id: uid(), y: clamp(L.y + 0.06, 0, 1) };
    S.layers.splice(layerIdx(L.id) + 1, 0, c); syncLayerEls(); select('layer', c.id); changed();
  }
  $('#tl-del').addEventListener('click', delSel);
  $('#tl-dup').addEventListener('click', dupLayer);

  // ---------- chọn ----------
  function select(k, id) {
    sel = { k, id };
    if (k === 'intro' && S.intro.enabled && t >= S.intro.duration) seek(Math.min(1.2, S.intro.duration * 0.6));
    if (k === 'outro' && S.outro.enabled && t < timeline().outStart) seek(timeline().outStart + Math.min(1.2, S.outro.duration * 0.6));
    renderInspector(); renderTimeline(); draw();
  }

  // ---------- tải file ----------
  const fileIn = $('#ed-file');
  async function upload(files) {
    const out = [];
    for (const f of files) {
      const fd = new FormData(); fd.append('file', f);
      toast('Đang tải ' + f.name + '…');
      try {
        const r = await fetch('/tpl/asset', { method: 'POST', body: fd });
        if (!r.ok) throw new Error(await r.text());
        const j = await r.json(); if (j.dur) fileDur[j.name] = j.dur; out.push(j);
      } catch (e) { toast(`Không tải được ${f.name}: ${e.message}`, true); }
    }
    if (out.length) { await loadAssets(); toast(`Đã thêm ${out.length} file vào thư viện`); }
    return out;
  }
  function pickFiles(accept, multi) {
    return new Promise((res) => {
      fileIn.accept = accept || ''; fileIn.multiple = !!multi; fileIn.value = '';
      fileIn.onchange = async () => res(fileIn.files.length ? await upload([...fileIn.files]) : []);
      fileIn.click();
    });
  }
  async function loadAssets() {
    try { assets = await (await fetch('/tpl/assets')).json(); } catch (e) { assets = []; }
    assets.forEach((a) => { if (a.dur) fileDur[a.name] = a.dur; });
    if (libTab === 'media' || libTab === 'audio') renderLib();
    renderTimeline();
  }

  // ---------- thư viện ----------
  const TEXTS = {
    title: { name: 'Tiêu đề', text: 'Tiêu đề của bạn', size: 0.055, color: '#ffffff', stroke: '#000000', stroke_w: 0.08, anim: 'slide_up', y: 0.2, end: 4 },
    boxed: { name: 'Chữ có nền', text: 'Chữ có nền', size: 0.04, color: '#ffffff', stroke_w: 0, box: true, box_color: '#0f766e', box_opacity: 0.92, anim: 'fade', y: 0.75 },
    yellow: { name: 'Vàng nổi bật', text: 'SIÊU HAY', size: 0.065, color: '#ffd400', stroke: '#000000', stroke_w: 0.11, anim: 'slide_up', y: 0.25, end: 3 },
    pink: { name: 'Viền hồng', text: 'Đáng yêu quá', size: 0.05, color: '#ffffff', stroke: '#ff2d75', stroke_w: 0.12, anim: 'fade', y: 0.3 },
    caption: { name: 'Phụ đề đen', text: 'Dòng chú thích', size: 0.034, color: '#ffffff', stroke_w: 0, box: true, box_color: '#000000', box_opacity: 0.6, anim: 'none', y: 0.85 },
    tag: { name: 'Nhãn đỏ', text: 'MỚI', size: 0.032, font: 'bold', color: '#ffffff', stroke_w: 0, box: true, box_color: '#e11d48', box_opacity: 1, anim: 'slide_left', x: 0.2, y: 0.12 },
    watermark: { name: 'Watermark', text: '@tenkenh', size: 0.028, color: '#ffffff', stroke: '#000000', stroke_w: 0.06, opacity: 0.8, anim: 'none', x: 0.82, y: 0.06 },
    cta: { name: 'Kêu gọi theo dõi', text: 'Theo dõi để xem phần tiếp!', size: 0.042, color: '#ffe14d', stroke: '#000000', stroke_w: 0.1, anim: 'slide_up', y: 0.82, startEnd: 4 },
  };
  const CARDS = [{ bg: '#101418', name: 'Thẻ đen' }, { bg: '#0f766e', name: 'Thẻ xanh' }, { bg: '#e11d48', name: 'Thẻ đỏ' }, { bg: '#f5f5f0', color: '#111111', name: 'Thẻ trắng' }];
  const FILTERS = [
    { n: 'Gốc', v: { brightness: 0, contrast: 1, saturation: 1, temperature: 6500, hue: 0, grain: 0, vignette: false, sharpen: 0 } },
    { n: 'Sáng rực', v: { brightness: 0.05, contrast: 1.08, saturation: 1.25, temperature: 6500, hue: 0, grain: 0, vignette: false, sharpen: 0.3 } },
    { n: 'Điện ảnh', v: { brightness: -0.03, contrast: 1.18, saturation: 0.85, temperature: 6900, hue: 0, grain: 4, vignette: true, sharpen: 0.2 } },
    { n: 'Ấm áp', v: { brightness: 0.02, contrast: 1.05, saturation: 1.1, temperature: 4800, hue: 0, grain: 0, vignette: false, sharpen: 0 } },
    { n: 'Mát lạnh', v: { brightness: 0.01, contrast: 1.05, saturation: 0.95, temperature: 8300, hue: 0, grain: 0, vignette: false, sharpen: 0 } },
    { n: 'Cổ điển', v: { brightness: 0.03, contrast: 0.9, saturation: 0.65, temperature: 5200, hue: 0, grain: 14, vignette: true, sharpen: 0 } },
    { n: 'Đen trắng', v: { brightness: 0, contrast: 1.2, saturation: 0, temperature: 6500, hue: 0, grain: 6, vignette: false, sharpen: 0.2 } },
    { n: 'Rực rỡ', v: { brightness: 0, contrast: 1.12, saturation: 1.65, temperature: 6500, hue: 0, grain: 0, vignette: false, sharpen: 0.4 } },
    { n: 'Mơ màng', v: { brightness: 0.07, contrast: 0.88, saturation: 0.9, temperature: 6000, hue: -8, grain: 0, vignette: false, sharpen: 0 } },
  ];
  const drag = (o) => `draggable="true" data-drag="${esc(JSON.stringify(o))}"`;
  $$('[data-lib]').forEach((b) => b.addEventListener('click', () => openLib(b.dataset.lib)));
  function openLib(k) {
    libTab = k;
    $$('[data-lib]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.lib === k)));
    renderLib();
  }
  function textStyle(o) {
    return `font-family:${o.font === 'bold' ? 'EDBold' : 'EDXBold'};color:${o.color};` +
      (o.stroke_w ? `-webkit-text-stroke:${Math.max(1.5, 15 * o.stroke_w * 2)}px ${o.stroke || '#000'};` : '') +
      (o.box ? `background:${o.box_color}${Math.round((o.box_opacity ?? 1) * 255).toString(16).padStart(2, '0')};padding:2px 6px;` : '');
  }
  function renderLib() {
    const k = libTab;
    let h = '';
    if (k === 'media') {
      const media = assets.filter((a) => a.kind !== 'audio');
      h = `<button type="button" class="ed-upload" data-act="upload" data-accept="video/*,image/*,audio/*"><b>＋ Nhập file</b>hoặc kéo thả video, ảnh, nhạc từ máy</button>
        <h3>Thẻ màu <span class="ed-note">kéo vào ô Intro / Outro</span></h3>
        <div class="ed-grid">${CARDS.map((c) => `<div class="ed-tile ed-ctile" ${drag({ type: 'card', bg: c.bg, color: c.color || '#ffffff' })}>
          <span class="th" style="background:${c.bg}"><b style="color:${c.color || '#fff'}">Tiêu đề</b><span class="add" data-act="add" title="Thêm làm intro">+</span></span><span class="nm">${c.name}</span></div>`).join('')}</div>
        <h3>Của bạn <span class="ed-note">${media.length} file</span></h3>
        ${media.length ? `<div class="ed-grid">${media.map((a) => `<div class="ed-tile" ${drag({ type: 'asset', name: a.name, kind: a.kind, dur: a.dur })} title="${esc(a.label)}">
          <span class="th" style="background-image:url(/tpl/thumb/${a.name})">${a.kind === 'video' ? `<span class="dur">${fmt(a.dur, 's')}</span>` : ''}
            <span class="add" data-act="add" title="${a.kind === 'video' ? 'Thêm làm intro / outro' : 'Thêm làm logo'}">+</span>
            <button type="button" class="x" data-act="hide:${a.name}" title="Bỏ khỏi thư viện">×</button></span>
          <span class="nm">${esc(a.label)}</span></div>`).join('')}</div>`
        : '<p class="ed-libempty">Chưa có file. Nhập video intro / outro, logo (ảnh PNG nền trong) rồi kéo vào dòng thời gian hoặc khung xem.</p>'}
        <p class="ed-note ed-libhint">Video → kéo vào ô Intro / Outro. Ảnh → kéo lên khung xem (logo) hoặc ô Intro / Outro (ảnh nền).</p>`;
    } else if (k === 'audio') {
      const au = assets.filter((a) => a.kind === 'audio');
      h = `<button type="button" class="ed-upload" data-act="upload" data-accept="audio/*"><b>＋ Nhập nhạc</b>mp3, m4a, wav, aac, ogg</button>
        <h3>Nhạc của bạn</h3>
        ${au.length ? `<div class="ed-alist">${au.map((a) => `<div class="ed-arow" ${drag({ type: 'asset', name: a.name, kind: 'audio' })} aria-pressed="${S.music.file === a.name}" title="Kéo xuống dòng Nhạc, hoặc bấm +">
          <i>♪</i><span>${esc(a.label)}</span><small>${fmt(a.dur, 's')} </small><button type="button" class="ed-b" data-act="add" title="Dùng làm nhạc nền">+</button></div>`).join('')}</div>`
        : '<p class="ed-libempty">Chưa có nhạc. Nhập file nhạc rồi kéo xuống dòng "Nhạc" ở dòng thời gian.</p>'}`;
    } else if (k === 'text') {
      h = `<h3>Kiểu chữ <span class="ed-note">kéo lên khung xem hoặc bấm +</span></h3>
        <div class="ed-grid">${Object.entries(TEXTS).map(([id, o]) => `<div class="ed-tile text" ${drag({ type: 'text', preset: id })}>
          <span class="th"><b style="${textStyle(o)}">${esc(o.text.length > 14 ? o.name : o.text)}</b><span class="add" data-act="add">+</span></span><span class="nm">${o.name}</span></div>`).join('')}</div>
        <h3>Logo</h3>
        <div class="ed-tile" ${drag({ type: 'logo' })}><button type="button" class="ed-upload" data-act="add"><b>＋ Logo / ảnh</b>Chọn ảnh PNG từ máy (hoặc kéo ô này lên khung xem)</button></div>`;
    } else if (k === 'trans') {
      h = `<h3>Chuyển cảnh <span class="ed-note">kéo lên hình thoi ở dòng thời gian</span></h3>
        <div class="ed-grid">${Object.entries(ED.trans).map(([v, lb]) => `<div class="ed-tile tr" ${drag({ type: 'trans', v })}>
          <span class="th"><i class="tr-ico tr-${v}"></i><span class="add" data-act="add">+</span></span><span class="nm">${esc(lb)}</span></div>`).join('')}</div>`;
    } else if (k === 'filter') {
      h = `<h3>Bộ lọc <span class="ed-note">bấm + hoặc kéo lên video</span></h3>
        <div class="ed-grid">${FILTERS.map((f, i) => {
          const on = Object.entries(f.v).every(([kk, vv]) => S.main[kk] === vv);
          const css = `brightness(${1 + f.v.brightness * 1.6}) contrast(${f.v.contrast}) saturate(${f.v.saturation}) hue-rotate(${f.v.hue}deg)` + (f.v.temperature < 6500 ? ' sepia(0.25)' : '');
          return `<div class="ed-tile" ${drag({ type: 'filter', i })} aria-pressed="${on}"><span class="th" style="${thumbURL ? `background-image:url(${thumbURL});` : ''}filter:${css}"><span class="add" data-act="add">+</span></span><span class="nm">${f.n}</span></div>`;
        }).join('')}</div>`;
    } else if (k === 'adjust') {
      h = `<h3>Điều chỉnh màu video</h3>${adjustHTML()}`;
    }
    libbody.innerHTML = h;
  }

  // ---------- áp dụng (thả / bấm +) ----------
  function addText(id, at = {}) {
    const o = TEXTS[id], D = timeline().D;
    let start = at.start ?? 0;
    if (o.startEnd && at.start == null) start = Math.max(0, D - o.startEnd);
    const L = { id: uid(), type: 'text', text: o.text, x: at.x ?? o.x ?? 0.5, y: at.y ?? o.y ?? 0.5, size: o.size, opacity: o.opacity ?? 1,
      start, end: o.end != null ? Math.min(D, start + o.end) : null, anim: o.anim || 'none', font: o.font || 'extrabold',
      color: o.color, stroke: o.stroke || '#000000', stroke_w: o.stroke_w ?? 0.08, box: !!o.box, box_color: o.box_color || '#000000',
      box_opacity: o.box_opacity ?? 0.5 };
    if (L.end != null && L.end <= L.start) L.end = null;
    S.layers.push(L); syncLayerEls(); select('layer', L.id);
    if (!at.keepTime) seek(timeline().mainStart + L.start + 0.6);
    changed();
  }
  function addImage(name, at = {}) {
    const L = { id: uid(), type: 'image', file: name, x: at.x ?? 0.15, y: at.y ?? 0.08, size: 0.18, opacity: 1, anim: 'none',
      start: at.start ?? 0, end: null };
    S.layers.push(L); syncLayerEls(); select('layer', L.id); changed();
  }
  function setSeg(k, j) {
    const s = S[k]; s.enabled = true;
    if (j.card) { s.file = ''; s.bg = j.bg || s.bg; s.color = j.color || s.color; if (!s.title) s.title = k === 'intro' ? 'Tiêu đề video' : 'Cảm ơn đã xem'; }
    else {
      s.file = j.name;
      const d = j.dur || fileDur[j.name] || asset(j.name)?.dur;
      if (isVidFile(j.name) && d > 0) {
        s.duration = clamp(Math.round(d * 10) / 10, 0.5, 30);
        if (s.title === 'Tiêu đề video' || s.title === 'Cảm ơn đã xem') { s.title = ''; s.sub = ''; }
        toast(`${k === 'intro' ? 'Intro' : 'Outro'} = video ${fmt(s.duration, 's')}` + (d > 30 ? ' (tối đa 30 s)' : ''));
      }
    }
    loadSegMedia(); itab[k] = 'content'; select(k); changed({ insp: true });
  }
  function setMusic(name) {
    S.music.file = name; loadMusic(); select('music'); changed({ insp: true, lib: libTab === 'audio' });
    toast('Đã thêm nhạc nền');
  }
  function applyFilter(i) { Object.assign(S.main, FILTERS[i].v); select('main'); changed({ lib: true }); toast('Bộ lọc: ' + FILTERS[i].n); }
  function setTrans(k, v) {
    if (!S[k].enabled) { toast(`Chưa có ${k} — thêm ${k} trước`, true); return; }
    S[k].transition = v; sel = { k: k === 'intro' ? 'tin' : 'tout' }; changed({ insp: true });
    const tl = timeline(); seek(Math.max(0, (k === 'intro' ? tl.mainStart : tl.outStart) - 0.6)); play(true);
  }
  function quickAdd(p) {
    if (p.type === 'text') return addText(p.preset);
    if (p.type === 'logo') return pickFiles('image/*', false).then((r) => r[0] && addImage(r[0].name));
    if (p.type === 'card') return setSeg(S.intro.enabled && !S.outro.enabled ? 'outro' : 'intro', p);
    if (p.type === 'filter') return applyFilter(p.i);
    if (p.type === 'trans') {
      const k = sel.k === 'tout' || sel.k === 'outro' ? 'outro' : sel.k === 'tin' || sel.k === 'intro' ? 'intro' : S.intro.enabled ? 'intro' : 'outro';
      return setTrans(k, p.v);
    }
    if (p.type === 'asset') {
      if (p.kind === 'audio') return setMusic(p.name);
      if (p.kind === 'image') return addImage(p.name);
      return setSeg(S.intro.enabled && !S.outro.enabled ? 'outro' : 'intro', { name: p.name, dur: p.dur });
    }
  }

  // ---------- kéo thả ----------
  let dragP = null, overEl = null;
  root.addEventListener('dragstart', (e) => {
    const tile = e.target.closest?.('[data-drag]'); if (!tile) return;
    dragP = JSON.parse(tile.dataset.drag); tile.classList.add('dragging');
    e.dataTransfer.setData('text/plain', 'dytool'); e.dataTransfer.effectAllowed = 'copy';
    document.body.classList.add('dnd');
    if (dragP.type === 'text' || dragP.type === 'logo' || (dragP.type === 'asset' && dragP.kind === 'image')) document.body.classList.add('dnd-frame');
  });
  root.addEventListener('dragend', () => endDrag());
  function endDrag() {
    dragP = null; document.body.classList.remove('dnd', 'dnd-frame', 'os-over');
    $$('.dragging').forEach((x) => x.classList.remove('dragging')); setOver(null);
  }
  function setOver(el) { if (overEl && overEl !== el) overEl.classList.remove('over'); overEl = el; if (el) el.classList.add('over'); }
  function accepts(zone, p) {
    if (!p) return false;
    const a = p.type === 'asset' ? p.kind : p.type;
    return !!({ frame: ['text', 'image', 'filter', 'logo'], main: ['filter'], intro: ['video', 'image', 'card', 'trans'], outro: ['video', 'image', 'card', 'trans'],
      tin: ['trans'], tout: ['trans'], layers: ['text', 'image', 'logo'], music: ['audio', 'video'] })[zone]?.includes(a);
  }
  const isFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
  document.addEventListener('dragover', (e) => {
    if (isFiles(e) && !dragP) {          // file từ Windows Explorer
      e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; document.body.classList.add('os-over');
      setOver(e.target.closest?.('[data-drop]') || null); return;
    }
    const z = e.target.closest?.('[data-drop]');
    if (z && accepts(z.dataset.drop, dragP)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setOver(z); } else setOver(null);
  });
  document.addEventListener('dragleave', (e) => { if (!e.relatedTarget) { document.body.classList.remove('os-over'); setOver(null); } });
  document.addEventListener('drop', async (e) => {
    e.preventDefault();
    const z = e.target.closest?.('[data-drop]');
    if (isFiles(e) && !dragP) {
      endDrag();
      const got = await upload([...e.dataTransfer.files]);
      if (z && got.length === 1) dropOn(z, { type: 'asset', name: got[0].name, kind: got[0].kind, dur: got[0].dur }, e);
      else if (got.length) openLib(got.every((g) => g.kind === 'audio') ? 'audio' : 'media');
      return;
    }
    const p = dragP; endDrag();
    if (z && accepts(z.dataset.drop, p)) dropOn(z, p, e);
  });
  function dropOn(z, p, e) {
    const zone = z.dataset.drop, a = p.type === 'asset' ? p.kind : p.type;
    if (!accepts(zone, p)) { toast('Không thả loại này vào đây được', true); return; }
    const tl = timeline();
    if (zone === 'frame') {
      const r = frame.getBoundingClientRect(), x = clamp((e.clientX - r.left) / r.width, 0, 1), y = clamp((e.clientY - r.top) / r.height, 0, 1);
      const start = t >= tl.mainStart && t < tl.mainEnd ? Math.round((t - tl.mainStart) * 10) / 10 : 0;
      if (a === 'text') return addText(p.preset, { x, y, start, keepTime: true });
      if (a === 'image') return addImage(p.name, { x, y, start });
      if (a === 'logo') return pickFiles('image/*', false).then((r2) => r2[0] && addImage(r2[0].name, { x, y, start }));
      return applyFilter(p.i);
    }
    if (zone === 'main') return applyFilter(p.i);
    if (zone === 'intro' || zone === 'outro') { if (a === 'trans') return setTrans(zone, p.v); return setSeg(zone, p.type === 'card' ? p : { name: p.name, dur: p.dur }); }
    if (zone === 'tin' || zone === 'tout') return setTrans(zone === 'tin' ? 'intro' : 'outro', p.v);
    if (zone === 'layers') {
      const r = inner.getBoundingClientRect();
      const start = clamp(Math.round((secOf(e.clientX - r.left) - tl.mainStart) * 10) / 10, 0, Math.max(0, tl.D - 0.5));
      if (a === 'text') return addText(p.preset, { start, keepTime: true });
      if (a === 'image') return addImage(p.name, { start });
      return pickFiles('image/*', false).then((r2) => r2[0] && addImage(r2[0].name, { start }));
    }
    if (zone === 'music') return setMusic(p.name);
  }

  // ---------- video xem thử ----------
  const sampleSel = $('#ed-sample');
  let lastSample = sampleSel.value;
  sampleSel.addEventListener('change', async () => {
    if (sampleSel.value === '__upload') {
      const j = (await pickFiles('video/*', false))[0];
      if (!j) { sampleSel.value = lastSample; return; }
      const o = new Option('Video tải lên · ' + j.label.slice(0, 28), 'a:' + j.name); o.dataset.url = '/tpl/asset/' + j.name;
      sampleSel.add(o, 0); sampleSel.value = o.value;
    }
    lastSample = sampleSel.value; loadSample();
  });
  $('#ed-up-sample').addEventListener('click', () => { sampleSel.value = '__upload'; sampleSel.dispatchEvent(new Event('change')); });
  function loadSample() {
    const o = sampleSel.selectedOptions[0];
    const url = o && o.value !== '__upload' ? o.dataset.url : '';
    $('#ed-empty').hidden = !!url; hasVideo = !!url;
    if (!url) { layout(); renderAll(); return; }
    V.src = url; BG.src = url;
    V.addEventListener('loadedmetadata', () => {
      srcDur = V.duration || 10; vw = V.videoWidth || 1080; vh = V.videoHeight || 1920;
      layout(); seek(Math.min(t, timeline().total)); grabThumb(); renderInspector(); renderTimeline();
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

  // ---------- bố cục khung xem ----------
  function canvasRatio() {
    const r = S.canvas.ratio;
    if (r === 'keep') return vw / vh;
    const [a, b] = r.split(':').map(Number); return a / b;
  }
  let FH = 640;
  function layout() {
    const box = stage.getBoundingClientRect();
    const aw = box.width - 24, ah = box.height - 20, r = canvasRatio();
    let w = aw, h = aw / r; if (h > ah) { h = ah; w = ah * r; }
    frame.style.width = Math.max(40, Math.round(w)) + 'px'; frame.style.height = Math.max(40, Math.round(h)) + 'px';
    FH = h;
    const fit = S.canvas.fit, same = Math.abs(vw / vh - r) < 0.01;
    V.style.objectFit = fit === 'crop' ? 'cover' : 'contain';
    BG.hidden = same || fit !== 'blur';
    draw();
  }

  // ---------- vẽ 1 khung xem trước ----------
  const segEls = { intro: $('#ed-seg-intro'), outro: $('#ed-seg-outro') };
  function transStyle(el, kind, p, incoming) {
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
    if (o.box) { el.style.background = o.box_color + Math.round(o.box_opacity * 255).toString(16).padStart(2, '0'); el.style.padding = `${fs * 0.35}px`; }
    else { el.style.background = 'none'; el.style.padding = '0'; }
  }
  function drawSeg(k, tl) {
    const el = segEls[k], s = S[k];
    let show = false, local = 0, p = -1, incoming = true;
    if (s.enabled) {
      if (k === 'intro') {
        show = t < s.duration; local = t;
        if (tl.tdIn && t > tl.mainStart) { p = (t - tl.mainStart) / tl.tdIn; incoming = false; }
      } else {
        show = t >= tl.outStart; local = t - tl.outStart;
        if (tl.tdOut && local < tl.tdOut) p = local / tl.tdOut;
      }
    }
    el.hidden = !show;
    el.classList.toggle('sel', sel.k === k);
    const v = $('video', el);
    if (!show) { if (!v.paused) v.pause(); return; }
    if (p >= 0) transStyle(el, s.transition, p, incoming); else { el.style.opacity = ''; el.style.clipPath = ''; el.style.transform = ''; el.style.filter = ''; }
    const f = s.file, img = isImgFile(f), vid = isVidFile(f);
    el.style.backgroundColor = s.bg;
    el.style.backgroundImage = img ? `url(/tpl/asset/${f})` : '';
    el.style.backgroundSize = s.fit === 'crop' ? 'cover' : 'contain';
    const sbg = $('.sg-bg', el);
    sbg.hidden = !(f && s.fit === 'blur');
    sbg.style.backgroundImage = f ? `url(/tpl/thumb/${f})` : '';
    if (vid && v.readyState >= 1) {
      v.style.objectFit = s.fit === 'crop' ? 'cover' : 'contain';
      const want = Math.min(local, Math.max(0, v.duration - 0.05));
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
  function syncLayerEls() {
    const lbox = $('#ed-lbox');
    const have = new Set($$('.ed-l', lbox).map((e) => e.dataset.id));
    S.layers.forEach((L) => {
      let el = lbox.querySelector(`[data-id="${L.id}"]`);
      const sig = L.type + (L.file || '');
      if (el && el.dataset.sig !== sig) { el.remove(); el = null; }
      if (!el) {
        el = document.createElement('div'); el.className = 'ed-l'; el.dataset.id = L.id; el.dataset.sig = sig;
        el.appendChild(L.type === 'text' ? document.createElement('span') : Object.assign(document.createElement('img'), { src: '/tpl/asset/' + L.file, alt: '' }));
        el.insertAdjacentHTML('beforeend', '<i class="rz" title="Kéo để đổi cỡ"></i>');
        lbox.appendChild(el); dragLayer(el);
      }
      have.delete(L.id);
    });
    have.forEach((id) => lbox.querySelector(`[data-id="${id}"]`)?.remove());
    S.layers.forEach((L) => { const el = lbox.querySelector(`[data-id="${L.id}"]`); if (el) lbox.appendChild(el); });   // thứ tự = thứ tự lớp
  }
  function draw() {
    const tl = timeline(), m = S.main;
    const local = t - tl.mainStart, inMain = t >= tl.mainStart && t < tl.mainEnd;
    if (hasVideo) {
      const want = tl.t0 + clamp(local, 0, tl.D) * m.speed;
      if (!playing || !inMain || V.paused || Math.abs(V.currentTime - want) > 0.3) {
        if (Math.abs(V.currentTime - want) > 0.05) V.currentTime = want;
      }
      if (Math.abs(BG.currentTime - V.currentTime) > 0.3) BG.currentTime = V.currentTime;
      if (playing && inMain) { V.playbackRate = m.speed; if (V.paused) V.play().catch(() => {}); if (!BG.hidden && BG.paused) BG.play().catch(() => {}); }
      else { if (!V.paused) V.pause(); if (!BG.paused) BG.pause(); }
      V.volume = Math.min(1, m.volume);
    }
    // video xem thử đã edit = đã có sẵn lật / màu phủ / nghiêng -> chỉ áp lại khi xem video tải lên (bản gốc)
    const b = sampleSel.value.startsWith('a:') ? S.base : { hflip: false, rotate_deg: 0, tint_color: '#000000', tint_opacity: 0 };
    const z = 1 + m.zoom * clamp(local / tl.D, 0, 1);
    const rot = b.rotate_deg ? `rotate(${b.rotate_deg}deg) scale(${1 + Math.abs(b.rotate_deg) * 0.035})` : '';
    $('#ed-mainwrap').style.transform = `scale(${z}) ${b.hflip ? 'scaleX(-1)' : ''} ${rot}`;
    BG.style.transform = b.hflip ? 'scaleX(-1)' : '';
    $('#ed-mainwrap').style.filter = `brightness(${1 + m.brightness * 1.6}) contrast(${m.contrast}) saturate(${m.saturation}) hue-rotate(${m.hue}deg)`;
    const tint = $('#ed-tint'); tint.style.background = b.tint_color; tint.style.opacity = b.tint_opacity;
    const temp = $('#ed-temp'), dk = (m.temperature - 6500) / 3500;
    temp.style.background = dk < 0 ? '#ff8a2a' : '#3a8bff'; temp.style.opacity = Math.min(0.35, Math.abs(dk) * 0.3);
    $('#ed-vig').hidden = !m.vignette;
    const gr = $('#ed-grain'); gr.style.opacity = m.grain / 70; gr.hidden = !m.grain;
    let fade = 1;
    if (!S.intro.enabled && m.fade_in && local < m.fade_in) fade = Math.max(0, local / m.fade_in);
    if (!S.outro.enabled && m.fade_out && local > tl.D - m.fade_out) fade = Math.min(fade, Math.max(0, (tl.D - local) / m.fade_out));
    frame.style.setProperty('--fade', fade);
    $$('#ed-lbox > .ed-l').forEach((el) => {
      const L = S.layers.find((x) => x.id === el.dataset.id); if (!L) return;
      const c = el.firstChild;
      el.style.left = L.x * 100 + '%'; el.style.top = L.y * 100 + '%';
      if (L.type === 'text') { c.textContent = L.text; styleText(c, L); } else c.style.width = L.size * frame.clientWidth + 'px';
      const end = L.end == null ? tl.D : Math.min(tl.D, L.end);
      const vis = inMain && local >= L.start && local <= end;
      if (inMain) textAnim(c, L.anim, local, L.start, end, L.opacity); else c.style.opacity = 0;
      const isSel = sel.k === 'layer' && sel.id === L.id;
      el.classList.toggle('sel', isSel);
      el.classList.toggle('ghost', isSel && !vis);
      el.style.pointerEvents = vis || isSel ? '' : 'none';
    });
    const bar = $('#ed-bar');
    bar.hidden = !S.progress.enabled || !inMain;
    bar.style.background = S.progress.color; bar.style.height = Math.max(2, S.progress.height * FH) + 'px';
    bar.style.width = clamp(local / tl.D, 0, 1) * 100 + '%';
    bar.style.top = S.progress.pos === 'top' ? '0' : ''; bar.style.bottom = S.progress.pos === 'top' ? '' : '0';
    drawSeg('intro', tl); drawSeg('outro', tl);
    if (music.src) {
      music.volume = Math.min(1, S.music.volume);
      if (playing) { const mt = t % (music.duration || 1e9); if (Math.abs(music.currentTime - mt) > 0.4) music.currentTime = mt; if (music.paused) music.play().catch(() => {}); }
      else if (!music.paused) music.pause();
    }
    $('#ed-time').textContent = clock(t); $('#ed-total').textContent = clock(tl.total);
    const ph = $('#tl-ph'); if (ph) ph.style.left = xOf(t) + 'px';
  }

  // ---------- phát ----------
  function loop(ts) {
    if (!playing) return;
    const dt = lastTs ? (ts - lastTs) / 1000 : 0; lastTs = ts;
    const tl = timeline();
    if (hasVideo && t >= tl.mainStart && t < tl.mainEnd - 0.05 && !V.paused) t = tl.mainStart + (V.currentTime - tl.t0) / S.main.speed;
    else t += dt;
    if (t >= tl.total) { t = tl.total; play(false); }
    draw(); followPlayhead();
    requestAnimationFrame(loop);
  }
  function play(on) {
    playing = on ?? !playing;
    if (playing && t >= timeline().total - 0.05) t = 0;
    root.classList.toggle('playing', playing); lastTs = 0;
    if (!playing) $$('.ed-seg video').forEach((v) => v.pause());
    if (playing) requestAnimationFrame(loop); else draw();
  }
  function seek(x) { t = clamp(x, 0, timeline().total); draw(); }
  $('#ed-play').addEventListener('click', () => play());
  $('#ed-ratio').addEventListener('change', (e) => { S.canvas.ratio = e.target.value; layout(); changed({ insp: true }); });
  document.addEventListener('keydown', (e) => {
    const a = document.activeElement;
    const typing = a && (/^(TEXTAREA|SELECT)$/.test(a.tagName) || (a.tagName === 'INPUT' && !['range', 'checkbox', 'color'].includes(a.type)));
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !typing) { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y' && !typing) { e.preventDefault(); redo(); return; }
    if (typing) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'd') { e.preventDefault(); dupLayer(); return; }
    if (e.code === 'Space') { e.preventDefault(); play(); }
    if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); delSel(); }
    if (e.key === 'ArrowLeft') seek(t - (e.shiftKey ? 1 : 0.1));
    if (e.key === 'ArrowRight') seek(t + (e.shiftKey ? 1 : 0.1));
    if (e.key === 'Escape') select('main');
  });

  // ---------- nền intro / outro + nhạc ----------
  function loadSegMedia() {
    for (const k of ['intro', 'outro']) {
      const el = segEls[k], v = $('video', el), f = S[k].file;
      const url = isVidFile(f) ? `/tpl/asset/${f}` : '';
      if (v.getAttribute('src') !== url) {
        if (url) { v.src = url; v.addEventListener('loadedmetadata', () => { fileDur[f] = v.duration; }, { once: true }); }
        else { v.removeAttribute('src'); v.load?.(); }
      }
      v.hidden = !url;
    }
  }
  function loadMusic() {
    music.pause();
    if (S.music.file) { music.src = '/tpl/asset/' + S.music.file; music.loop = true; } else music.removeAttribute('src');
  }

  // ---------- kéo lớp trên khung xem ----------
  function dragLayer(el) {
    el.addEventListener('pointerdown', (e) => {
      const L = S.layers.find((x) => x.id === el.dataset.id); if (!L) return;
      e.preventDefault(); e.stopPropagation();
      if (!(sel.k === 'layer' && sel.id === L.id)) select('layer', L.id);
      const r = frame.getBoundingClientRect();
      el.setPointerCapture(e.pointerId);
      let mv;
      if (e.target.classList.contains('rz')) {           // đổi cỡ: theo khoảng cách tới tâm lớp
        const cx = r.left + L.x * r.width, cy = r.top + L.y * r.height, d0 = Math.hypot(e.clientX - cx, e.clientY - cy) || 1, s0 = L.size;
        mv = (ev) => { L.size = clamp(s0 * Math.hypot(ev.clientX - cx, ev.clientY - cy) / d0, 0.01, L.type === 'text' ? 0.15 : 0.8); draw(); refreshOutputs(); };
      } else {
        const ox = e.clientX - L.x * r.width, oy = e.clientY - L.y * r.height;
        mv = (ev) => {
          let x = (ev.clientX - ox) / r.width, y = (ev.clientY - oy) / r.height;
          const sx = Math.abs(x - 0.5) < 0.015, sy = Math.abs(y - 0.5) < 0.015;
          if (sx) x = 0.5;
          if (sy) y = 0.5;
          $('#ed-gv').hidden = !sx; $('#ed-gh').hidden = !sy;
          L.x = clamp(x, 0, 1); L.y = clamp(y, 0, 1); draw();
        };
      }
      let moved = false;
      const mv0 = mv; mv = (ev) => { moved = true; mv0(ev); };
      const up = () => {
        el.removeEventListener('pointermove', mv);
        $('#ed-gv').hidden = true; $('#ed-gh').hidden = true; if (moved) changed();
      };
      el.addEventListener('pointermove', mv); el.addEventListener('pointerup', up, { once: true });
    });
  }
  $('#ed-mainwrap').addEventListener('pointerdown', () => { if (sel.k !== 'main') select('main'); });
  Object.entries(segEls).forEach(([k, el]) => el.addEventListener('pointerdown', () => { if (sel.k !== k) select(k); }));

  // ---------- dòng thời gian ----------
  const scroller = $('#tl-scroll'), inner = $('#tl-inner');
  const HEADW = 96, ORIGIN = HEADW + 100, TAIL = 110;     // cột nhãn (dính trái) + chỗ cho ô "+ Intro"
  let zoom = 1, pxPerS = 40;
  const xOf = (s) => ORIGIN + s * pxPerS;
  const secOf = (x) => (x - ORIGIN) / pxPerS;
  const ICO = {
    text: '<svg viewBox="0 0 24 24"><path d="M5 6V4h14v2M12 4v16M9 20h6"/></svg>',
    video: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14"/><path d="m10 9 5 3-5 3z"/></svg>',
    music: '<svg viewBox="0 0 24 24"><path d="M9 18V6l10-2v12"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="16" r="2"/></svg>',
    image: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16"/><circle cx="9" cy="9" r="2"/><path d="m20 15-5-5L5 20"/></svg>',
  };
  function renderTimeline() {
    const tl = timeline();
    const fitPx = Math.max(10, (scroller.clientWidth - ORIGIN - TAIL) / Math.max(1, tl.total));
    pxPerS = fitPx * zoom;
    const W = Math.max(scroller.clientWidth, xOf(tl.total) + TAIL);
    // mỗi hàng: ô nhãn dính trái (.tl-hd) + các khối — nhãn luôn thẳng hàng với hàng của nó
    const hd = (ico, txt) => `<div class="tl-hd">${ico}<span>${txt}</span></div>`;
    let h = '';
    const step = pxPerS > 90 ? 1 : pxPerS > 40 ? 2 : pxPerS > 18 ? 5 : 10;
    h += `<div class="tl-ruler">${hd('', '')}`;
    for (let s = 0; s <= tl.total + 0.001; s += step) h += `<span style="left:${xOf(s)}px">${clock(s).replace(/\.\d$/, '')}</span>`;
    for (let s = 0; s <= tl.total; s += step / 5) h += `<i style="left:${xOf(s)}px"></i>`;
    h += '</div>';
    // lớp chữ / logo — trên cùng = lớp sau cùng (như CapCut)
    h += `<div class="tl-row r-new" data-drop="layers">${hd(ICO.text, 'Lớp mới')}
      <div class="tl-empty" style="left:${xOf(tl.mainStart)}px" data-act="lib:text">＋ Kéo chữ / logo vào đây</div></div>`;
    [...S.layers].reverse().forEach((L) => {
      const end = L.end == null ? tl.D : Math.min(tl.D, L.end);
      const isSel = sel.k === 'layer' && sel.id === L.id;
      h += `<div class="tl-row r-layer" data-drop="layers">${hd(L.type === 'text' ? ICO.text : ICO.image, L.type === 'text' ? 'Chữ' : 'Logo')}
        <div class="tl-b ${L.type === 'text' ? 'b-text' : 'b-image'}${isSel ? ' sel' : ''}" data-tl="layer" data-id="${L.id}"
          style="left:${xOf(tl.mainStart + L.start)}px;width:${Math.max(6, (end - L.start) * pxPerS)}px">
          <i class="h l" data-h="l"></i><span>${esc(L.type === 'text' ? L.text.split('\n')[0] || '(trống)' : label(L.file))}</span><i class="h r" data-h="r"></i></div></div>`;
    });
    // video chính + intro / outro
    h += `<div class="tl-row r-main">${hd(ICO.video, 'Video')}`;
    const mA = tl.mainStart + tl.tdIn / 2, mB = tl.mainEnd - tl.tdOut / 2;
    if (S.intro.enabled) h += segBlock('intro', 0, mA);
    else h += `<div class="tl-b b-slot" data-drop="intro" data-act="enable:intro" style="left:${HEADW + 8}px;width:84px" title="Bấm để thêm thẻ intro, hoặc kéo video / ảnh vào"><span>＋ Intro</span></div>`;
    h += `<div class="tl-b b-main${sel.k === 'main' ? ' sel' : ''}" data-tl="main" data-drop="main" style="left:${xOf(mA)}px;width:${(mB - mA) * pxPerS}px;${thumbURL ? `background-image:url(${thumbURL})` : ''}">
      <i class="h l" data-h="l" title="Kéo để cắt bỏ đầu video"></i><span>Video chính · ${fmt(tl.D, 's')}${S.main.trim_start || S.main.trim_end ? ' (đã cắt)' : ''}</span><i class="h r" data-h="r" title="Kéo để cắt bỏ cuối video"></i></div>`;
    if (S.outro.enabled) h += segBlock('outro', mB, tl.total);
    else h += `<div class="tl-b b-slot" data-drop="outro" data-act="enable:outro" style="left:${xOf(tl.mainEnd) + 4}px;width:84px" title="Bấm để thêm thẻ outro, hoặc kéo video / ảnh vào"><span>＋ Outro</span></div>`;
    for (const [k, at, td] of [['intro', tl.mainStart, tl.tdIn], ['outro', tl.outStart, tl.tdOut]]) {
      if (!S[k].enabled) continue;
      const sk = k === 'intro' ? 'tin' : 'tout';
      h += `<button type="button" class="tl-x${td ? '' : ' cut'}${sel.k === sk ? ' sel' : ''}" data-tl="${sk}" data-drop="${sk}" style="left:${xOf(at + td / 2)}px" title="Chuyển cảnh: ${esc(ED.trans[S[k].transition] || '')} — bấm để đổi, hoặc kéo chuyển cảnh từ thư viện vào"></button>`;
    }
    h += '</div>';
    h += `<div class="tl-row r-music" data-drop="music">${hd(ICO.music, 'Nhạc')}` + (S.music.file
      ? `<div class="tl-b b-music${sel.k === 'music' ? ' sel' : ''}" data-tl="music" style="left:${ORIGIN}px;width:${tl.total * pxPerS}px"><span>♪ ${esc(label(S.music.file))} · ${fmt(S.music.volume, 'pct')}</span></div>`
      : `<div class="tl-empty" style="left:${ORIGIN}px" data-act="lib:audio">♪ Kéo nhạc nền vào đây</div>`) + '</div>';
    h += `<div class="tl-ph" id="tl-ph" style="left:${xOf(t)}px"></div>`;
    inner.style.width = W + 'px';
    inner.innerHTML = h;
  }
  function segBlock(k, a, b) {
    const s = S[k], f = s.file;
    const bg = f ? `background-image:url(/tpl/thumb/${f})` : `background-color:${s.bg}`;
    return `<div class="tl-b b-${k}${sel.k === k ? ' sel' : ''}" data-tl="${k}" data-drop="${k}" style="left:${xOf(a)}px;width:${Math.max(8, (b - a) * pxPerS)}px;${bg}">
      <span>${k === 'intro' ? 'Intro' : 'Outro'} · ${fmt(s.duration, 's')}</span><i class="h r" data-h="r" title="Kéo để đổi độ dài"></i></div>`;
  }
  function followPlayhead() {
    const x = xOf(t);
    if (x > scroller.scrollLeft + scroller.clientWidth - 40 || x < scroller.scrollLeft + HEADW) scroller.scrollLeft = Math.max(0, x - HEADW - 80);
  }
  inner.addEventListener('pointerdown', (e) => {
    if (e.target.closest('[data-act], .tl-hd')) return;           // ô "+ Intro", "Kéo nhạc…" -> nút bấm
    const blk = e.target.closest('[data-tl]');
    const r = inner.getBoundingClientRect();
    if (!blk) {                                          // thước / chỗ trống -> tua
      const go = (ev) => seek(secOf(ev.clientX - r.left));
      go(e); inner.setPointerCapture(e.pointerId);
      inner.addEventListener('pointermove', go);
      inner.addEventListener('pointerup', () => inner.removeEventListener('pointermove', go), { once: true });
      return;
    }
    const kind = blk.dataset.tl, hdl = e.target.dataset.h || '';
    const tl = timeline();
    if (kind === 'layer') { if (!(sel.k === 'layer' && sel.id === blk.dataset.id)) select('layer', blk.dataset.id); }
    else if (kind !== sel.k) select(kind);
    if (kind === 'tin' || kind === 'tout') return;
    if (kind === 'music' || (kind !== 'layer' && !hdl)) { seek(secOf(e.clientX - r.left)); return; }
    e.preventDefault();
    const x0 = e.clientX, m = S.main, L = curLayer();
    const st = { ts: m.trim_start, te: m.trim_end, d: kind === 'intro' || kind === 'outro' ? S[kind].duration : 0,
      s0: L?.start, e0: L ? (L.end == null ? tl.D : L.end) : 0 };
    const snapPts = [0, tl.D, t - tl.mainStart, ...S.layers.filter((x) => x !== L).flatMap((x) => [x.start, x.end ?? tl.D])];
    const snap = (v) => { for (const p of snapPts) if (Math.abs(v - p) * pxPerS < 7) return p; return v; };
    inner.setPointerCapture(e.pointerId);
    let moved = false;
    const mv = (ev) => {
      if (!moved && Math.abs(ev.clientX - x0) < 3) return;      // bấm để chọn, không phải kéo
      moved = true;
      const ds = (ev.clientX - x0) / pxPerS;
      if (kind === 'main') {
        if (hdl === 'l') { m.trim_start = clamp(Math.round((st.ts + ds * m.speed) * 10) / 10, 0, Math.max(0, srcDur - st.te - 1)); seek(timeline().mainStart); }
        else { m.trim_end = clamp(Math.round((st.te - ds * m.speed) * 10) / 10, 0, Math.max(0, srcDur - m.trim_start - 1)); seek(timeline().mainEnd - 0.05); }
      } else if (kind === 'intro' || kind === 'outro') {
        S[kind].duration = clamp(Math.round((st.d + ds) * 10) / 10, 0.5, 30);
      } else if (kind === 'layer' && L) {
        const len = st.e0 - st.s0;
        if (!hdl) { L.start = clamp(Math.round(snap(st.s0 + ds) * 10) / 10, 0, Math.max(0, tl.D - 0.3)); L.end = Math.min(tl.D, L.start + len); }
        if (hdl === 'l') L.start = clamp(Math.round(snap(st.s0 + ds) * 10) / 10, 0, st.e0 - 0.3);
        if (hdl === 'r') L.end = clamp(Math.round(snap(st.e0 + ds) * 10) / 10, L.start + 0.3, tl.D);
        if (L.end != null && L.end >= tl.D - 0.05) L.end = null;
        seek(tl.mainStart + (hdl === 'r' ? (L.end ?? tl.D) - 0.05 : L.start + 0.05));
      }
      renderTimeline(); draw(); refreshOutputs();
    };
    const up = () => { inner.removeEventListener('pointermove', mv); if (moved) changed(); };
    inner.addEventListener('pointermove', mv); inner.addEventListener('pointerup', up, { once: true });
  });
  const setZoom = (z) => { zoom = clamp(z, 1, 8); $('#tl-zoom').value = zoom; renderTimeline(); followPlayhead(); };
  $('#tl-zoom').addEventListener('input', (e) => setZoom(Number(e.target.value)));
  $('#tl-zin').addEventListener('click', () => setZoom(zoom * 1.4));
  $('#tl-zout').addEventListener('click', () => setZoom(zoom / 1.4));
  $('#tl-fit').addEventListener('click', () => setZoom(1));
  scroller.addEventListener('wheel', (e) => { if (e.ctrlKey) { e.preventDefault(); setZoom(zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)); } }, { passive: false });

  // ---------- ảnh nhỏ video chính (bộ lọc + dải video) ----------
  let thumbURL = '';
  function grabThumb() {
    const go = () => {
      try {
        const c = document.createElement('canvas'), k = 120 / Math.max(vw, vh);
        c.width = Math.round(vw * k); c.height = Math.round(vh * k);
        c.getContext('2d').drawImage(V, 0, 0, c.width, c.height);
        thumbURL = c.toDataURL('image/jpeg', 0.7);
        renderTimeline(); if (libTab === 'filter') renderLib();
      } catch (e) { /* chưa có khung */ }
    };
    if (V.readyState >= 2) go(); else V.addEventListener('loadeddata', go, { once: true });
  }

  // ---------- làm lại từ đầu / chép sang kênh ----------
  $('#ed-reset').addEventListener('click', () => {
    if (!confirm('Xoá hết intro, outro, chữ, logo, nhạc và hiệu ứng của mẫu kênh này? (Ctrl+Z để lấy lại)')) return;
    pushHist();
    const base = S.base; S = Object.assign(structuredClone(ED.default), { base });
    sel = { k: 'main' }; loadSegMedia(); loadMusic(); layout(); renderAll(); changed();
  });
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
    play(false); dlg.showModal();
    rbody.innerHTML = '<p class="rd-wait"><i class="ed-spin"></i><span>Đang render bằng FFmpeg… (video ngắn khoảng 10–60 giây)</span></p>';
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
        if (j.state === 'running') { const sp = rbody.querySelector('.rd-wait span'); if (sp) sp.textContent = `Đang render bằng FFmpeg… ${Math.round(j.elapsed)} s`; continue; }
        if (j.state === 'error') throw new Error(j.error);
        rbody.innerHTML = `<video src="${j.url}" controls autoplay playsinline></video>
          <p class="ed-note" style="margin-top:8px">Bản thật (${full ? 'đủ cỡ' : 'cỡ nhỏ'}) · dài ${j.dur.toFixed(1)} s · render mất ${Math.round(j.took)} s.
          Video xem thử là bản đã edit sẵn của kênh (phụ đề / lồng tiếng cũ). ${full ? '' : '<button type="button" class="ed-b" id="rd-full">Render đủ cỡ</button>'}</p>`;
        $('#rd-full')?.addEventListener('click', () => renderTest(true));
        return;
      }
    } catch (e) { rbody.innerHTML = `<p class="rd-err">Không render được: ${esc(e.message)}</p>`; }
  }

  // ---------- khởi động ----------
  function renderAll() { syncLayerEls(); renderLib(); renderInspector(); renderTimeline(); refreshOutputs(); draw(); }
  new ResizeObserver(() => { layout(); renderTimeline(); }).observe(stage);
  $('#ed-undo').disabled = true; $('#ed-redo').disabled = true;
  loadSegMedia(); loadMusic(); renderAll(); loadSample(); layout(); loadAssets();
})();

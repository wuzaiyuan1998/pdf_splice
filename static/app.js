/* ============================================================
   PDF 单面拼接器 —— 前端逻辑
   三列：PDF_A | PDF_B | PDF_out
   拖拽用 HTML5 原生 DnD，服务端只存状态，前端按快照整体重绘
   ============================================================ */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const app = {
  data: null,      // 服务端状态快照
  drag: null,      // 当前拖拽的东西 {type:'src'|'out', ...}
  zoom: null,      // 放大预览 {frame,index}；frame 为 'A' / 'B'（源栏）或 'OUT'（成品栏）
  pickSlot: 'A',   // 文件选择框当前归属
  view: 'grid',    // 布局样式：grid（网格） | preview（竖向预览）
};

/* ------------------------------------------------ 请求层 */

async function request(url, options) {
  const res = await fetch(url, options);
  const type = res.headers.get('content-type') || '';
  if (!type.includes('json')) {
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return null;
  }
  let data = null;
  try { data = await res.json(); } catch (_) { data = null; }
  if (data && data.ok === false) throw new Error(data.error || '操作失败');
  if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
  return data;
}

const post = (url, body) => request(url, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body || {}),
});

/* ------------------------------------------------ 小工具 */

/**
 * 缩略图 URL。除「栏位 + 叠内位置 + 旋转 + 尺寸」外，还要带上该位置对应的**物理页码**。
 *
 * 后端给缩略图设了长缓存（max-age=86400），前提是 URL 能完全决定图片内容。倒序只改变
 * 「叠内位置 → 物理页」的对应关系，位置和旋转都没变，所以必须靠 p= 让 URL 跟着变，
 * 否则倒序后浏览器会拿着同一个 URL 命中旧图，界面看起来像没反应。
 */
const thumbUrl = (slot, index, rot, size = 240) => {
  const doc = app.data && app.data.docs[slot];
  const phys = doc && doc.order && doc.order[index] != null ? doc.order[index] : index;
  return `/api/thumb/${slot}/${index}?r=${((rot % 360) + 360) % 360}&size=${size}&p=${phys}`;
};

/* 网格样式取小图（一屏多页），竖向预览样式取大图（按栏宽铺满整页） */
const thumbSizeFor = (kind) => (app.view === 'preview' ? 760 : kind === 'row' ? 120 : 240);

function fmtSize(bytes) {
  if (!bytes) return '0 B';
  const mb = bytes / 1048576;
  return mb >= 1 ? mb.toFixed(2) + ' MB' : (bytes / 1024).toFixed(0) + ' KB';
}

function toast(message, kind = '', ms = 3200) {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .25s'; }, ms - 260);
  setTimeout(() => el.remove(), ms);
}

const span = (cls, text) => {
  const el = document.createElement('span');
  el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
};

const toolBtn = (act, text, title) => {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.dataset.act = act;
  btn.title = title || '';
  btn.textContent = text;
  return btn;
};

async function guard(promise, okMessage) {
  try {
    const data = await promise;
    if (data) apply(data);
    if (okMessage) toast(okMessage, 'ok');
    return true;
  } catch (err) {
    toast(err.message || String(err), 'err', 5000);
    return false;
  }
}

/* ------------------------------------------------ 状态同步 */

function apply(snapshot) {
  app.data = snapshot;
  if (!$('#lightbox').classList.contains('hidden')) renderLightbox();
  renderAll();
}

async function refresh() {
  try {
    app.data = await request('/api/state');
    renderAll();
  } catch (err) {
    toast('连接后端失败：' + err.message, 'err', 6000);
  }
}

function renderAll() {
  if (!app.data) return;
  renderDoc('A');
  renderDoc('B');
  renderOut();
  syncControls();
}

function renderStats() {
  const a = app.data.docs.A, b = app.data.docs.B, out = app.data.out;
  const modeText = { interleave: '自动交错', concat: '顺序拼接', manual: '手动排布' }[app.data.mode] || '';
  $('#stats').textContent =
    `A：${a.loaded ? a.page_count + ' 页' : '—'}　B：${b.loaded ? b.page_count + ' 页' : '—'}　` +
    `成品：${out.length} 页　·　${modeText}`;
}

/* ------------------------------------------------ 第一 / 第二列 */

function renderDoc(slot) {
  const doc = app.data.docs[slot];
  const col = $(`.col[data-slot="${slot}"]`);
  const box = $('#pages' + slot);

  col.querySelector('.fname').textContent = doc.loaded
    ? `${doc.name} · ${doc.page_count} 页 · ${fmtSize(doc.size)}`
    : '未载入';
  col.querySelector('.count').textContent = doc.loaded
    ? doc.page_count + ' 页' + (doc.reversed ? ' · 已倒序' : '')
    : '';

  // 「倒序」按钮反映当前叠序状态：已倒序时高亮，未载入时禁用
  const revBtn = col.querySelector('button[data-act="reverse"]');
  if (revBtn) {
    revBtn.classList.toggle('on', !!doc.reversed);
    revBtn.disabled = !doc.loaded;
    revBtn.title = doc.reversed
      ? '这一叠当前已倒序 · 再点一次恢复原顺序'
      : '把这一叠的页序整个倒过来；成品内容保持原样';
  }

  box.textContent = '';
  if (!doc.loaded) {
    const hint = document.createElement('div');
    hint.className = 'empty-hint';
    hint.innerHTML = '点击这里载入 <b>PDF</b><br>或把文件拖进本栏<br>可一次选多个文件（按顺序合并成一叠）';
    hint.addEventListener('click', () => pickFile(slot));
    box.appendChild(hint);
    return;
  }
  doc.pages.forEach((p) => box.appendChild(thumbCard(slot, p.index, doc.rots[p.index])));
}

function thumbCard(slot, index, rot) {
  const card = document.createElement('div');
  card.className = 'page';
  card.draggable = true;
  card.dataset.slot = slot;
  card.dataset.index = String(index);
  card.title = `PDF_${slot} 第 ${index + 1} 页 · 点击放大预览`;

  const thumb = document.createElement('div');
  thumb.className = 'ph-thumb';
  const img = document.createElement('img');
  img.src = thumbUrl(slot, index, rot, thumbSizeFor('card'));
  img.alt = slot + (index + 1);
  img.draggable = false;
  img.loading = 'lazy';
  thumb.appendChild(img);

  const meta = document.createElement('div');
  meta.className = 'ph-meta';
  meta.append(span('tag', slot + (index + 1)), span('rot', rot ? rot + '°' : ''));

  const tools = document.createElement('div');
  tools.className = 'ph-tools';
  tools.append(
    toolBtn('rot-l', '↺', '逆时针 90°'),
    toolBtn('rot-r', '↻', '顺时针 90°'),
    toolBtn('add', '＋', '插入到成品末尾'),
  );

  card.append(thumb, meta, tools);
  return card;
}

/* ------------------------------------------------ 第三列（成品） */

function renderOut() {
  const box = $('#pagesOut');
  const out = app.data.out;

  $('#outCount').textContent = out.length ? out.length + ' 页' : '';
  $('#outInfo').textContent = out.length
    ? `成品 ${out.length} 页 · ${({ interleave: '自动交错', concat: '顺序拼接', manual: '手动排布' })[app.data.mode] || ''}`
    : '成品（空）';

  box.textContent = '';
  if (!out.length) {
    const hint = document.createElement('div');
    hint.className = 'empty-hint';
    hint.innerHTML = '成品还是空的<br>点上方 <b>生成成品</b> 自动交错<br>或把 A / B 的页面拖进来手动排';
    box.appendChild(hint);
    return;
  }
  out.forEach((item, pos) => box.appendChild(outRow(item, pos)));
}

function outRow(item, pos) {
  const row = document.createElement('div');
  row.className = 'out-row';
  row.draggable = true;
  row.dataset.slot = 'OUT';
  row.dataset.pos = String(pos);
  row.dataset.srcSlot = item.slot;
  row.dataset.srcIndex = String(item.index);
  row.title = '来自 PDF_' + item.slot + ' 第 ' + (item.index + 1) + ' 页 · 拖动可重排 · 点击可放大';

  const thumb = document.createElement('div');
  thumb.className = 'thumb';
  const img = document.createElement('img');
  img.src = thumbUrl(item.slot, item.index, item.rot, thumbSizeFor('row'));
  img.alt = '';
  img.draggable = false;
  img.loading = 'lazy';
  thumb.appendChild(img);

  const from = document.createElement('div');
  from.className = 'from';
  from.innerHTML = `<b>${item.slot}${item.index + 1}</b> · 来自 PDF_${item.slot} 第 ${item.index + 1} 页`;
  if (item.rot % 360) from.appendChild(span('rot-tag', '· 转 ' + item.rot + '°'));

  const tools = document.createElement('div');
  tools.className = 'row-tools';
  tools.append(
    toolBtn('rot-l', '↺', '逆时针 90°'),
    toolBtn('rot-r', '↻', '顺时针 90°'),
    toolBtn('del', '×', '从成品中移除'),
  );

  row.append(span('idx', String(pos + 1)), thumb, from, tools);
  return row;
}

/* ------------------------------------------------ 控件状态 */

function syncControls() {
  const d = app.data;
  $$('#segMode button').forEach((b) => b.classList.toggle('on', b.dataset.mode === d.mode));
  $$('#segFirst button').forEach((b) => b.classList.toggle('on', b.dataset.first === d.first));
  $('#chkReverse').checked = !!d.reverse_second;

  // 「撤销删除」按钮跟随服务端的可撤销状态
  const undoBtn = $('#btnUndo');
  if (undoBtn) {
    const u = d.undo || {};
    undoBtn.disabled = !u.available;
    undoBtn.textContent = u.available ? `↶ 撤销删除 (${u.count})` : '↶ 撤销删除';
    undoBtn.title = u.available
      ? `撤销「${u.label}」，恢复 ${u.count} 页`
      : '撤销最近一次删除（成品结构改动后无法撤销）';
  }
  renderStats();
}

/* 布局样式：网格 / 竖向预览。换样式要重新取图（两种样式的缩略图尺寸不同） */
function setView(view) {
  app.view = view === 'preview' ? 'preview' : 'grid';
  document.documentElement.dataset.view = app.view;
  localStorage.setItem('pdf_splice_view', app.view);
  $$('#segView button').forEach((b) => b.classList.toggle('on', b.dataset.view === app.view));
  renderAll();
}

/* 只改参数：本地先更新，再让服务端重建（有载入 PDF 时才真的重建） */
function setBuild(patch) {
  const payload = {
    mode: patch.mode ?? app.data.mode,
    first: patch.first ?? app.data.first,
    reverse_second: patch.reverse_second ?? app.data.reverse_second,
  };
  if (payload.mode === 'manual') payload.mode = 'interleave';
  return guard(post('/api/build', payload));
}

/* ------------------------------------------------ 文件载入 */

function pickFile(slot) {
  app.pickSlot = slot;
  const input = $('#fileInput');
  input.value = '';           // 允许重复选同一个文件
  input.click();
}

async function uploadFiles(slot, files) {
  const pdfs = files.filter((f) => /\.pdf$/i.test(f.name) || f.type === 'application/pdf');
  if (!pdfs.length) { toast('只支持 PDF 文件', 'err'); return; }
  if (pdfs.length < files.length) toast('已忽略 ' + (files.length - pdfs.length) + ' 个非 PDF 文件', 'warn');

  const fd = new FormData();
  fd.append('slot', slot);
  pdfs.forEach((f) => fd.append('file', f));

  const tip = pdfs.length > 1 ? `正在合并载入 ${pdfs.length} 个文件到 PDF_${slot}…` : `正在载入 PDF_${slot}…`;
  toast(tip);
  const data = await request('/api/upload', { method: 'POST', body: fd });
  apply(data);
  toast(`PDF_${slot} 载入完成：${data.docs[slot].page_count} 页`, 'ok');
}

/* ------------------------------------------------ 勾选空白页 */

const BLANK_LABEL = { strict: '严格', normal: '标准', loose: '宽松' };

let blankPreset = 'normal';        // 弹窗里当前选中的灵敏度
let blankScan = null;              // 最近一次扫描结果（rows = 成品全部页的墨迹指标）
let blankPick = new Set();         // 已勾选的成品位置（默认 = 自动命中的那些）
let blankShowAll = false;          // 是否列出成品的每一页，而不只是自动命中的

/** 打一次 /api/out/blank：只扫描，服务端不改任何状态。 */
async function blankScanRequest(preset) {
  const snap = await request('/api/out/blank', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ preset }),
  });
  apply(snap);
  return snap.blank || {};
}

async function openBlankDialog() {
  if (!app.data || !app.data.out.length) return toast('成品还是空的，先点「生成成品」', 'warn');
  blankPreset = localStorage.getItem('pdf_splice_blank_preset') || 'normal';
  if (!BLANK_LABEL[blankPreset]) blankPreset = 'normal';
  blankShowAll = false;
  $('#chkBlankAll').checked = false;
  $$('#segBlank button').forEach((b) => b.classList.toggle('on', b.dataset.preset === blankPreset));
  $('#blankModal').classList.remove('hidden');
  $('#blankModal').setAttribute('aria-hidden', 'false');
  return refreshBlankScan();
}

function closeBlankDialog() {
  $('#blankModal').classList.add('hidden');
  $('#blankModal').setAttribute('aria-hidden', 'true');
  blankScan = null;
  blankPick = new Set();
}

/**
 * 重新扫描（打开弹窗、切灵敏度都会走这里）。
 * 扫描只读，勾选一律重置成「全部自动命中的页」—— 换档位后旧勾选已经没有意义了。
 */
async function refreshBlankScan() {
  blankScan = null;
  blankPick = new Set();
  const body = $('#blankBody');
  body.textContent = '';
  const wait = document.createElement('div');
  wait.className = 'modal-empty';
  wait.textContent = '正在分析成品每一页的墨迹…';
  body.appendChild(wait);
  $('#blankSub').textContent = '正在扫描…';
  $('#blankNote').textContent = '';
  syncBlankPick();                                   // 计数归零、删除按钮禁用
  try {
    blankScan = await blankScanRequest(blankPreset);
  } catch (err) {
    toast(err.message || String(err), 'err', 5000);
    return closeBlankDialog();
  }
  blankPick = new Set((blankScan.pages || []).map((p) => p.pos));   // 命中页默认勾上
  renderBlankDialog();
}

function renderBlankDialog() {
  const scan = blankScan || {};
  $('#blankSub').textContent = `${BLANK_LABEL[scan.preset] || scan.preset || ''}灵敏度 · ` +
    `扫描 ${scan.scanned || 0} 页 · 自动命中 ${(scan.pages || []).length} 页`;
  renderBlankGrid();
  syncBlankPick();
}

function renderBlankGrid() {
  const scan = blankScan || {};
  const rows = scan.rows || [];
  // 「显示全部页」= 成品每一页；否则只列自动命中的页 —— 外加用户手动勾上的页，
  // 这样任何被勾中的页都一定看得见（否则关掉开关后会留下「看不见却被选中」的页）。
  const shown = blankShowAll ? rows : rows.filter((r) => r.blank || blankPick.has(r.pos));

  const body = $('#blankBody');
  body.textContent = '';
  if (!shown.length) {
    const div = document.createElement('div');
    div.className = 'modal-empty';
    div.innerHTML = `扫了 <b>${scan.scanned || 0}</b> 页，没自动发现空白页。<br>` +
      '可以换更宽松的灵敏度，或者勾上「显示全部页」手动挑要删的页。';
    body.appendChild(div);
    return;
  }

  const grid = document.createElement('div');
  grid.className = 'blank-grid';
  shown.forEach((p) => grid.appendChild(blankCell(p)));
  body.appendChild(grid);
}

function blankCell(page) {
  const item = (app.data.out || [])[page.pos] || {};
  const cell = document.createElement('div');
  cell.className = 'blank-cell' + (page.blank ? '' : ' miss') + (blankPick.has(page.pos) ? ' on' : '');
  cell.dataset.pos = String(page.pos);
  // 注意：这里不能叫 data-blank —— 弹窗里 [data-blank] 是「动作按钮」的命名空间
  // （全选 / 全不选 / 取消 / 确认），卡片再用同名属性会被当成动作按钮吞掉点击
  cell.dataset.hit = page.blank ? '1' : '0';
  cell.title = `成品第 ${page.pos + 1} 页 · 点卡片勾选 / 取消，点右上角放大确认`;

  const thumb = document.createElement('div');
  thumb.className = 'ph-thumb';
  const img = document.createElement('img');
  img.src = thumbUrl(page.slot, page.index, item.rot || 0, 240);
  img.alt = page.label;
  img.loading = 'lazy';
  thumb.appendChild(img);

  // 勾选框：真 input，键盘也能操作；点卡片其它地方由上面的监听器代劳
  const pick = document.createElement('label');
  pick.className = 'cell-pick';
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.checked = blankPick.has(page.pos);
  box.setAttribute('aria-label', `勾选成品第 ${page.pos + 1} 页`);
  pick.appendChild(box);

  const zoom = document.createElement('button');
  zoom.type = 'button';
  zoom.className = 'cell-zoom';
  zoom.dataset.zoom = '1';
  zoom.title = '放大确认这一页';
  zoom.textContent = '🔍';

  const meta = document.createElement('div');
  meta.className = 'cell-meta';
  meta.innerHTML = `<b>成品第 ${page.pos + 1} 页</b> ` +
    (page.blank ? '<span class="tag">判为空白</span>' : '<span class="tag miss">未判为空白</span>') +
    `<br>来自 PDF_${page.slot} 第 ${page.index + 1} 页<br>` +
    `墨迹 ${(page.dark_ratio * 100).toFixed(3)}%`;

  cell.append(thumb, pick, zoom, meta);
  return cell;
}

/** 勾选变化后同步：卡片高亮 / 计数 / 删除按钮文案。所有勾选改动都汇总到这里。 */
function syncBlankPick() {
  const n = blankPick.size;
  $('#blankPickInfo').textContent = `已选 ${n} 页`;
  const go = $('#btnBlankGo');
  go.disabled = n === 0;
  go.textContent = n ? `删除勾选的 ${n} 页` : '删除勾选的页';
  $$('#blankBody .blank-cell').forEach((c) => {
    const on = blankPick.has(Number(c.dataset.pos));
    c.classList.toggle('on', on);
    const box = c.querySelector('input[type=checkbox]');
    if (box) box.checked = on;
  });
  $('#chkBlankAll').checked = blankShowAll;
  if (blankScan) {
    $('#blankNote').textContent = n
      ? `已勾选 ${n} 页 · 点右侧按钮才会真正删除，删完可「↶ 撤销删除」恢复`
      : '还没有勾选任何页 —— 不会有页面被删掉';
  }
}

/** 「全选命中」：把自动判定命中的页全部勾上（重新扫描后就是这个状态）。 */
function selectAllHits() {
  blankPick = new Set(((blankScan && blankScan.pages) || []).map((p) => p.pos));
  syncBlankPick();
}

async function runBlankDelete() {
  const positions = Array.from(blankPick).sort((a, b) => a - b);
  if (!positions.length) return;
  const btn = $('#btnBlankGo');
  btn.disabled = true;
  try {
    const snap = await post('/api/out/remove', { positions });
    apply(snap);
    closeBlankDialog();
    toast(`已删除勾选的 ${positions.length} 页，成品剩 ${snap.out_count} 页\n` +
      '删错了点「↶ 撤销删除」即可恢复', 'ok', 5600);
  } catch (err) {
    toast(err.message || String(err), 'err', 5000);
    btn.disabled = false;
  }
}

async function undoRemove() {
  try {
    const snap = await request('/api/out/undo', { method: 'POST' });
    apply(snap);
    toast(`已撤销删除，恢复 ${snap.restored} 页`, 'ok');
  } catch (err) {
    toast(err.message || String(err), 'err', 5000);
  }
}

/* 弹窗事件（注册在大图预览的监听器之前，Esc 才能让大图优先关） */

$('#segBlank').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-preset]');
  if (!btn || btn.dataset.preset === blankPreset) return;
  blankPreset = btn.dataset.preset;
  localStorage.setItem('pdf_splice_blank_preset', blankPreset);
  $$('#segBlank button').forEach((b) => b.classList.toggle('on', b === btn));
  refreshBlankScan();                 // 指标有缓存，换档位是秒回
});

/* 「显示全部页」只是换渲染范围，不用重扫：rows 里已经有每一页的指标了 */
$('#chkBlankAll').addEventListener('change', (e) => {
  blankShowAll = e.target.checked;
  if (!blankScan) return;
  renderBlankGrid();
  syncBlankPick();
});

/* 勾选框自己被点（含点 label、键盘空格）：浏览器已翻好状态，这里只同步 */
$('#blankBody').addEventListener('change', (e) => {
  const box = e.target.closest('input[type=checkbox]');
  const cell = box && box.closest('.blank-cell');
  if (!cell) return;
  const pos = Number(cell.dataset.pos);
  if (box.checked) blankPick.add(pos); else blankPick.delete(pos);
  syncBlankPick();
});

document.addEventListener('click', (e) => {
  const hit = e.target.closest('#blankModal [data-blank]');
  if (hit) {
    const what = hit.dataset.blank;
    if (what === 'cancel') return closeBlankDialog();
    if (what === 'confirm') return runBlankDelete();
    if (what === 'all') return selectAllHits();
    if (what === 'none') { blankPick = new Set(); return syncBlankPick(); }
    return;
  }
  if ($('#blankModal').classList.contains('hidden')) return;

  const cell = e.target.closest('.blank-cell');
  if (cell) {
    if (e.target.closest('[data-zoom]')) return openLightbox('OUT', Number(cell.dataset.pos));
    if (e.target.closest('.cell-pick')) return;        // 交给上面的 change 监听
    const pos = Number(cell.dataset.pos);              // 点卡片 = 勾选 / 取消
    if (blankPick.has(pos)) blankPick.delete(pos); else blankPick.add(pos);
    return syncBlankPick();
  }
  if (e.target.id === 'blankModal') closeBlankDialog();   // 点遮罩关闭
});

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (!$('#lightbox').classList.contains('hidden')) return;   // 大图开着时 Esc 只关大图
  if (!$('#blankModal').classList.contains('hidden')) closeBlankDialog();
});

/* ------------------------------------------------ 事件：点击 */

document.addEventListener('click', async (e) => {
  // 1) 栏头工具按钮
  const actBtn = e.target.closest('button[data-act]');
  if (actBtn) {
    const act = actBtn.dataset.act;
    const slot = actBtn.dataset.slot || actBtn.closest('.page')?.dataset.slot;

    if (act === 'pick') return pickFile(slot);

    if (act === 'rot-all') {
      const delta = Number(actBtn.dataset.delta || 90);
      return guard(post('/api/rotate', { slot, delta, index: null }));
    }
    if (act === 'reverse') {
      const doc = app.data.docs[slot];
      if (!doc || !doc.loaded) return toast(`PDF_${slot} 还没载入 PDF`, 'warn');
      const wasReversed = !!doc.reversed;
      return guard(post('/api/reverse_slot', { slot }),
        wasReversed
          ? `PDF_${slot} 已恢复原页序`
          : `PDF_${slot} 已倒序 · 成品内容未改动，想按新顺序请重新点「生成成品」`);
    }
    if (act === 'add-all') return guard(post('/api/out/insert', { slot, all: true }));

    if (act === 'blank') return openBlankDialog();
    if (act === 'undo') {
      if (!app.data.undo || !app.data.undo.available) return;
      return undoRemove();
    }

    if (act === 'clear') {
      if (!confirm(`确定清空 PDF_${slot} 吗？（成品里来自这一叠的页面也会移除）`)) return;
      return guard(post('/api/clear_slot', { slot }));
    }

    // 2) 缩略图上的按钮
    const card = actBtn.closest('.page');
    if (card) {
      const index = Number(card.dataset.index);
      if (act === 'rot-l') return guard(post('/api/rotate', { slot, index, delta: -90 }));
      if (act === 'rot-r') return guard(post('/api/rotate', { slot, index, delta: 90 }));
      if (act === 'add') return guard(post('/api/out/insert', { slot, index }));
    }

    // 3) 成品行上的按钮
    const row = actBtn.closest('.out-row');
    if (row) {
      const pos = Number(row.dataset.pos);
      if (act === 'rot-l') return guard(post('/api/out/rotate', { positions: [pos], delta: -90 }));
      if (act === 'rot-r') return guard(post('/api/out/rotate', { positions: [pos], delta: 90 }));
      if (act === 'del') return guard(post('/api/out/remove', { positions: [pos] }));
    }
    return;
  }

  // 4) 点击缩略图 → 放大预览（弹窗里的卡片归弹窗自己的监听器管：点卡片 = 勾选）
  const card = e.target.closest('.page');
  if (card) return openLightbox(card.dataset.slot, Number(card.dataset.index));

  const row = e.target.closest('.out-row');
  // 成品页按「成品列表」的坐标预览：翻页要沿着合并后的顺序走，而不是回到来源那一叠
  if (row) return openLightbox('OUT', Number(row.dataset.pos));
});

/* ------------------------------------------------ 事件：栏头 / 参数控件 */

$('#segMode').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-mode]');
  if (btn) setBuild({ mode: btn.dataset.mode });
});
$('#segFirst').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-first]');
  if (btn) setBuild({ first: btn.dataset.first });
});
$('#chkReverse').addEventListener('change', (e) => setBuild({ reverse_second: e.target.checked }));

$('#segView').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-view]');
  if (btn) setView(btn.dataset.view);
});

$('#btnBuild').addEventListener('click', () => {
  if (!app.data.docs.A.loaded && !app.data.docs.B.loaded) return toast('先在 A / B 里载入 PDF', 'warn');
  const mode = app.data.mode === 'manual' ? 'interleave' : app.data.mode;
  guard(post('/api/build', { mode, first: app.data.first, reverse_second: app.data.reverse_second }),
    '成品已重建');
});

$('#btnAutosort').addEventListener('click', async () => {
  try {
    const snap = await request('/api/autosort', { method: 'POST' });
    apply(snap);
    const info = snap.autosort || {};
    toast(info.note || '已完成', info.ok ? 'ok' : 'warn', 5200);
  } catch (err) { toast(err.message, 'err', 5000); }
});

$('#btnClearOut').addEventListener('click', () => {
  if (!app.data.out.length) return;
  if (!confirm('确定清空成品吗？')) return;
  guard(post('/api/out/clear'));
});

$('#btnSwap').addEventListener('click', () => guard(post('/api/swap'), '已交换 A / B'));

$('#btnOpenDir').addEventListener('click', async () => {
  try {
    const r = await post('/api/open_output');
    toast('已打开输出目录：' + r.path, 'ok');
  } catch (err) { toast(err.message, 'err'); }
});

$('#btnExport').addEventListener('click', async () => {
  if (!app.data.out.length) return toast('成品还是空的，先点「生成成品」', 'warn');
  const btn = $('#btnExport');
  btn.disabled = true;
  try {
    const res = await post('/api/export', { name: $('#outName').value.trim() });
    toast(`已生成 ${res.name}（${res.pages} 页 / ${fmtSize(res.size)}）\n保存于：${res.path}`, 'ok', 7000);
    const a = document.createElement('a');
    a.href = res.url;
    a.download = res.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } catch (err) {
    toast(err.message, 'err', 6000);
  } finally {
    btn.disabled = false;
  }
});

/* 文件选择框（A / B 共用） */
$('#fileInput').addEventListener('change', async (e) => {
  const files = Array.from(e.target.files || []);
  if (!files.length) return;
  try { await uploadFiles(app.pickSlot, files); } catch (err) { toast(err.message, 'err', 6000); }
});

/* ------------------------------------------------ 事件：拖拽 */

document.addEventListener('dragstart', (e) => {
  const row = e.target.closest ? e.target.closest('.out-row') : null;
  const card = e.target.closest ? e.target.closest('.page') : null;
  let info = null;
  if (row) info = { type: 'out', pos: Number(row.dataset.pos) };
  else if (card) info = { type: 'src', slot: card.dataset.slot, index: Number(card.dataset.index) };
  if (!info) return;

  app.drag = info;
  e.dataTransfer.effectAllowed = 'move';
  try { e.dataTransfer.setData('text/plain', JSON.stringify(info)); } catch (_) { /* 忽略 */ }
  (row || card).classList.add('dragging');
  document.body.classList.add('dragging-active');
});

document.addEventListener('dragend', () => {
  app.drag = null;
  $$('.dragging').forEach((el) => el.classList.remove('dragging'));
  document.body.classList.remove('dragging-active');
  $$('.col.drag-over').forEach((el) => el.classList.remove('drag-over'));
  removePlaceholder();
});

function dropIndex(clientY) {
  const rows = $$('.out-row', $('#pagesOut'));
  for (let i = 0; i < rows.length; i += 1) {
    const r = rows[i].getBoundingClientRect();
    if (clientY < r.top + r.height / 2) return i;
  }
  return rows.length;
}

function showPlaceholder(index) {
  const box = $('#pagesOut');
  let ph = box.querySelector('.ph');
  if (!ph) {
    ph = document.createElement('div');
    ph.className = 'ph';
  }
  const hint = box.querySelector('.empty-hint');
  if (hint) hint.remove();
  const rows = $$('.out-row', box);
  box.insertBefore(ph, rows[index] || null);
}

function removePlaceholder() {
  const box = $('#pagesOut');
  const ph = box.querySelector('.ph');
  if (ph) ph.remove();
  if (app.data && !app.data.out.length && !box.querySelector('.empty-hint')) renderOut();
}

$$('.col').forEach((col) => {
  const slot = col.dataset.slot;

  col.addEventListener('dragover', (e) => {
    const dt = e.dataTransfer || {};
    const files = Array.from(dt.types || []).includes('Files');
    if (files) {
      if (slot === 'OUT') return;                 // 外部文件只能拖到 A / B
      e.preventDefault();
      dt.dropEffect = 'copy';
      col.classList.add('drag-over');
      return;
    }
    if (!app.drag) return;
    e.preventDefault();
    dt.dropEffect = 'move';
    col.classList.add('drag-over');
    if (slot === 'OUT') showPlaceholder(dropIndex(e.clientY));
  });

  col.addEventListener('dragleave', (e) => {
    if (e.relatedTarget && col.contains(e.relatedTarget)) return;
    col.classList.remove('drag-over');
    if (slot === 'OUT') removePlaceholder();
  });

  col.addEventListener('drop', async (e) => {
    const info = app.drag;
    const files = e.dataTransfer && e.dataTransfer.files;

    // ① 从资源管理器拖文件进来
    if (files && files.length && slot !== 'OUT') {
      e.preventDefault();
      col.classList.remove('drag-over');
      try { await uploadFiles(slot, Array.from(files)); } catch (err) { toast(err.message, 'err', 6000); }
      return;
    }

    if (!info) return;
    e.preventDefault();
    col.classList.remove('drag-over');

    if (slot === 'OUT') {
      removePlaceholder();
      const at = dropIndex(e.clientY);
      if (info.type === 'src') {
        await guard(post('/api/out/insert', { slot: info.slot, index: info.index, at }));
      } else {
        await guard(post('/api/out/move', { from: info.pos, to: at }));
      }
    } else if (info.type === 'out') {
      // 把成品页拖回源栏 = 从成品里删掉
      const ok = await guard(post('/api/out/remove', { positions: [info.pos] }));
      if (ok) toast('已从成品移除该页', 'ok');
    }
    removePlaceholder();
  });
});

/* ------------------------------------------------ 放大预览 */

/**
 * 把 app.zoom 解析成「当前正在预览的那一页」。
 *
 * 源栏（A / B）：坐标就是该叠里的页码。
 * 成品栏（OUT）：坐标是成品列表里的位置；缩略图仍取自它来源的那一页。
 * 遍历一律按「当前所在栏」的总页数走，所以成品栏翻页是 80 页而不是来源的 40 页。
 *
 * 返回 { frame, index, total, rot, thumbSlot, thumbIndex, title, alt }，无效时返回 null。
 */
function lightboxTarget() {
  if (!app.zoom || !app.data) return null;
  const { frame, index } = app.zoom;

  if (frame === 'OUT') {
    const out = app.data.out || [];
    if (!(index >= 0 && index < out.length)) return null;
    const item = out[index];
    return {
      frame,
      index,
      total: out.length,
      rot: item.rot,
      thumbSlot: item.slot,
      thumbIndex: item.index,
      title: `PDF_out 第 ${index + 1} / ${out.length} 页 · 来自 PDF_${item.slot} 第 ${item.index + 1} 页`,
      alt: `out${index + 1}`,
    };
  }

  const doc = app.data.docs[frame];
  if (!doc || !doc.loaded || !(index >= 0 && index < doc.page_count)) return null;
  return {
    frame,
    index,
    total: doc.page_count,
    rot: doc.rots[index],
    thumbSlot: frame,
    thumbIndex: index,
    title: `PDF_${frame} 第 ${index + 1} / ${doc.page_count} 页`,
    alt: `${frame}${index + 1}`,
  };
}

function openLightbox(frame, index) {
  if (!app.data) return;
  app.zoom = { frame, index };
  if (!lightboxTarget()) {          // 越界（比如那一栏是空的）就不开
    app.zoom = null;
    return;
  }
  renderLightbox();
  $('#lightbox').classList.remove('hidden');
}

function closeLightbox() {
  app.zoom = null;
  $('#lightbox').classList.add('hidden');
  $('#lbImg').removeAttribute('src');
}

function renderLightbox() {
  const t = lightboxTarget();
  if (!t) return closeLightbox();
  const rot = ((t.rot % 360) + 360) % 360;
  $('#lbTitle').textContent = t.title + (rot ? ` · 已旋转 ${rot}°` : '');
  const img = $('#lbImg');
  img.src = thumbUrl(t.thumbSlot, t.thumbIndex, t.rot, 1400);
  img.alt = t.alt;
}

function zoomStep(delta) {
  const t = lightboxTarget();
  if (!t) return;
  app.zoom.index = (t.index + delta + t.total) % t.total;
  renderLightbox();
}

/**
 * 预览里旋转。两者的语义不同，必须分开调：
 *  源页 → /api/rotate（会连带更新成品里由它生成、且没被单独转过的页）
 *  成品页 → /api/out/rotate（只转成品里的这一格，不动源页）
 */
function rotateZoomed(delta) {
  const t = lightboxTarget();
  if (!t) return;
  const req = t.frame === 'OUT'
    ? post('/api/out/rotate', { positions: [t.index], delta })
    : post('/api/rotate', { slot: t.frame, index: t.index, delta });
  guard(req, null).then(() => renderLightbox());
}

document.addEventListener('click', (e) => {
  const btn = e.target.closest('#lightbox [data-lb]');
  if (!btn) {
    if (e.target.id === 'lightbox' || e.target.classList.contains('lb-stage')) closeLightbox();
    return;
  }
  const act = btn.dataset.lb;
  if (act === 'close') return closeLightbox();
  if (act === 'prev') return zoomStep(-1);
  if (act === 'next') return zoomStep(1);
  if (!app.zoom) return;
  rotateZoomed(act === 'rot-l' ? -90 : 90);
});

document.addEventListener('keydown', (e) => {
  if (!app.zoom) return;
  if (e.key === 'Escape') closeLightbox();
  else if (e.key === 'ArrowLeft' || e.key === 'PageUp') zoomStep(-1);
  else if (e.key === 'ArrowRight' || e.key === 'PageDown') zoomStep(1);
  else if (e.key === 'r' || e.key === 'R') rotateZoomed(90);
});

/* ------------------------------------------------ 主题 */

function initTheme() {
  const saved = localStorage.getItem('pdf_splice_theme');
  const auto = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  document.documentElement.dataset.theme = saved || auto;
}
$('#btnTheme').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('pdf_splice_theme', next);
});

/* ------------------------------------------------ 启动 */

initTheme();
setView(localStorage.getItem('pdf_splice_view') || 'grid');
refresh();

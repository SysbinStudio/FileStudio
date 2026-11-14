/* FileStudio 浏览器端逻辑 */
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const ENCODINGS = ['UTF-8', 'UTF-8-SIG', 'GBK', 'UTF-16', 'Big5', 'Latin-1'];
const ADMIN_ACTS = new Set(['save', 'saveas', 'export', 'system']);
const THEMES = {
  dark:  { bg: '#1b1e26', panel: '#252a35', fg: '#e7ebf3', accent: '#5b8cff', select: '#3a4a6b' },
  light: { bg: '#f2f4f8', panel: '#ffffff', fg: '#20242c', accent: '#2f6fe0', select: '#d6e4ff' },
  eye:   { bg: '#e9f0e4', panel: '#f6faf2', fg: '#2c3529', accent: '#4e8a54', select: '#c9dfc2' },
  warm:  { bg: '#2a2320', panel: '#352c27', fg: '#f2e8dd', accent: '#e0914f', select: '#5d4433' }
};

const S = {
  root: '', cwd: '', parent: '', items: [], selected: null, file: null,
  view: 'welcome', dirty: false, encoding: 'UTF-8',
  cfg: null, drives: [], port: 1111, sid: '',
  role: 'guest', guestEnabled: true,
  sortKey: '', sortRev: false, searchMode: false,
  zoom: 1, panX: 0, panY: 0, rot: 0, imgW: 0, imgH: 0,
  imageList: [], pdfMode: 'viewer', findIndex: 0
};

/* ------------------------------------------------------------ 基础工具 */

function esc(s) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(s === undefined || s === null ? '' : s).replace(/[&<>"']/g, (c) => map[c]);
}
const q = (s) => encodeURIComponent(s);
/* <img> / <iframe> / CSS 背景不能自定义请求头，会话 ID 走 URL 参数；
   Cookie 里同时有 HttpOnly 副本，接口请求统一用请求头。 */
function withSid(url) {
  if (!S.sid) return url;
  return url + (url.includes('?') ? '&' : '?') + 'sid=' + encodeURIComponent(S.sid);
}
async function api(url, options = {}) {
  const opts = Object.assign({}, options);
  const headers = Object.assign({}, opts.headers || {});
  if (S.sid) headers['X-FileStudio-Session'] = S.sid;
  opts.headers = headers;
  opts.credentials = 'same-origin';
  const res = await fetch(url, opts);
  let data;
  try { data = await res.json(); }
  catch (e) {
    const err = new Error('HTTP ' + res.status + '（返回内容不是 JSON）');
    err.status = res.status; throw err;
  }
  if (!data.ok) {
    const err = new Error(data.error || '请求失败');
    err.status = res.status;
    err.needLogin = !!data.need_login;
    if (res.status === 401 && !url.startsWith('/api/login')) {
      S.sid = ''; S.role = 'guest';
      localStorage.removeItem('fs_sid');
      applyRole();
      showLogin('登录已过期或服务需要登录，请重新输入管理员密码');
    } else if (res.status === 403 && data.need_login) {
      S.role = 'guest'; S.sid = '';
      localStorage.removeItem('fs_sid');
      applyRole();
      toast('登录状态已失效，当前为游客预览；请重新登录管理员', 'err');
    }
    throw err;
  }
  return data;
}
const GET = (url) => api(url);
const POST = (url, body) => api(url, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {})
});

let toastTimer = null;
function toast(msg, type = 'ok') {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'show' + (type === 'err' ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ''; }, 3000);
}
const setStatus = (t) => { $('#status').textContent = t; };
const setDetail = (t) => { $('#status-right').textContent = t; };

/* ------------------------------------------------------------ 弹窗 */

function openModal(title, bodyHtml, buttons) {
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = bodyHtml;
  const foot = $('#modal-foot');
  foot.innerHTML = '';
  (buttons || []).forEach((b) => {
    const btn = document.createElement('button');
    btn.textContent = b.label;
    if (b.primary) btn.classList.add('primary');
    if (b.danger) btn.classList.add('danger');
    btn.onclick = async () => {
      if (b.onClick) {
        const r = await b.onClick();
        if (r === false) return;
      }
      if (!b.keepOpen) closeModal();
    };
    foot.appendChild(btn);
  });
  $('#modal-mask').classList.remove('hidden');
}
function closeModal() {
  $('#modal-mask').classList.add('hidden');
  $('#modal-body').innerHTML = '';
  $('#modal-foot').innerHTML = '';
}
function showLogin(msg) {
  $('#login').classList.remove('hidden');
  $('#login-msg').textContent = msg || '';
  const guestBtn = $('#login-guest');
  guestBtn.classList.toggle('hidden', !S.guestEnabled);
  guestBtn.disabled = !S.guestEnabled;
  $('#login-pass').value = '';
  setTimeout(() => {
    const u = $('#login-user');
    (u.value.trim() ? $('#login-pass') : u).focus();
  }, 50);
}
function hideLogin() { $('#login').classList.add('hidden'); }
const isAdmin = () => S.role === 'admin';

/* 游客模式：界面层禁用所有写操作，服务端仍会再做一次权限校验 */
function applyRole() {
  const admin = isAdmin();
  ['#btn-root', '#btn-newfile', '#btn-newfolder', '#btn-upload', '#btn-rename',
   '#btn-delete', '#btn-settings', '#btn-explorer'].forEach((sel) => {
    const el = $(sel);
    if (!el) return;
    el.disabled = !admin;
    el.title = admin ? '' : '游客仅可预览，登录管理员后可用';
  });
  $('#editor').readOnly = !admin;
  const badge = $('#btn-auth');
  badge.textContent = admin ? '管理员 · 退出登录' : '游客预览 · 登录';
  badge.classList.toggle('guest', !admin);
  badge.title = admin ? '退出登录，切换为游客' : '用管理员账号登录以编辑文件';
  renderTools(S.view);
}
function requireAdmin() {
  if (isAdmin()) return true;
  toast('游客仅可预览，请登录管理员后再执行此操作', 'err');
  return false;
}

/* ------------------------------------------------------------ 外观 */

function applyConfig() {
  const c = S.cfg;
  if (!c) return;
  const r = document.documentElement.style;
  r.setProperty('--bg', c.bg);
  r.setProperty('--panel', c.panel);
  r.setProperty('--fg', c.fg);
  r.setProperty('--accent', c.accent);
  r.setProperty('--select', c.select);
  r.setProperty('--bg-opacity', String(1 - Number(c.bg_fade || 0)));
  r.setProperty('--editor-size', (c.font_size || 13) + 'px');
  const bg = $('#bg');
  if (c.bg_image) {
    bg.style.backgroundImage = 'url("' + withSid('/api/bg?t=' + Date.now()) + '")';
    bg.style.backgroundSize = ({ fit: 'contain', stretch: '100% 100%', center: 'cover', tile: 'auto' })[c.bg_mode] || 'contain';
    bg.style.backgroundRepeat = c.bg_mode === 'tile' ? 'repeat' : 'no-repeat';
  } else {
    bg.style.backgroundImage = 'none';
  }
}
function updateTitle() {
  $('#view-sub').textContent = S.file
    ? S.file.name + (S.dirty ? '  ● 未保存' : '') + '  ·  ' + (S.file.label || '')
    : '';
}

/* ------------------------------------------------------------ 目录树 */

function makeTreeNode(path, name, hasChildren) {
  const wrap = document.createElement('div');
  wrap.className = 'tree-node';
  const line = document.createElement('div');
  line.className = 'tree-line';
  line.dataset.path = path;
  const arrow = document.createElement('span');
  arrow.className = 'tree-arrow' + (hasChildren ? '' : ' empty');
  arrow.textContent = hasChildren ? '▸' : '·';
  const label = document.createElement('span');
  label.className = 'tree-label';
  label.textContent = name || path;
  line.append(arrow, label);
  const kids = document.createElement('div');
  kids.className = 'tree-children hidden';
  wrap.append(line, kids);
  line.addEventListener('click', (e) => { e.stopPropagation(); setCwd(path); });
  arrow.addEventListener('click', async (e) => {
    e.stopPropagation();
    await toggleNode(kids, path, arrow);
  });
  return wrap;
}
async function toggleNode(kids, path, arrow) {
  if (kids.classList.contains('hidden')) {
    if (!kids.dataset.loaded) {
      try {
        const r = await GET('/api/tree?path=' + q(path));
        kids.innerHTML = r.items.length
          ? ''
          : '<div class="tree-empty">（没有子目录）</div>';
        r.items.forEach((d) => kids.appendChild(makeTreeNode(d.path, d.name, d.has_children)));
        kids.dataset.loaded = '1';
      } catch (e) { toast('展开目录失败：' + e.message, 'err'); return; }
    }
    kids.classList.remove('hidden');
    arrow.textContent = '▾';
  } else {
    kids.classList.add('hidden');
    arrow.textContent = '▸';
  }
}
function highlightTree(path) {
  $$('#tree .tree-line').forEach((el) => el.classList.toggle('active', el.dataset.path === path));
}
async function loadTree() {
  const tree = $('#tree');
  tree.innerHTML = '';
  const label = S.root.split(/[\\/]/).filter(Boolean).pop() || S.root;
  const node = makeTreeNode(S.root, label + '（存储空间）', true);
  tree.appendChild(node);
  await toggleNode(node.querySelector('.tree-children'), S.root, node.querySelector('.tree-arrow'));
  highlightTree(S.root);
}

/* ------------------------------------------------------------ 文件列表 */

function iconFor(it) {
  const map = { image: '▧', office: '▤', pdf: '▤', archive: '▦', audio: '♫', video: '▶', binary: '◇' };
  return it.is_dir ? '▣' : (map[it.category] || (it.editable ? '▤' : '◇'));
}
function renderList(items) {
  const body = $('#file-body');
  if (!items.length) {
    body.innerHTML = '<tr><td class="empty-row" colspan="4">这里还没有文件</td></tr>';
  } else {
    body.innerHTML = items.map((it) => (
      '<tr data-path="' + esc(it.path) + '" class="' + (it.is_dir ? 'dir' : '') + '">' +
      '<td title="' + esc(it.name) + '"><span class="icon">' + iconFor(it) + '</span>' + esc(it.name) + '</td>' +
      '<td>' + esc(it.short_label) + '</td><td>' + esc(it.size_h || '—') + '</td><td>' + esc(it.mtime) + '</td></tr>'
    )).join('');
  }
  $('#list-count').textContent = items.length + ' 项';
}
async function loadList(path) {
  try {
    const data = await GET('/api/list?path=' + q(path || S.cwd));
    S.cwd = data.path;
    S.parent = data.parent;
    S.items = data.items;
    S.searchMode = false;
    S.imageList = data.items.filter((i) => !i.is_dir && i.category === 'image');
    $('#path').textContent = data.path;
    $('#path').title = data.path;
    $('#list-title').textContent = '文件列表';
    renderList(data.items);
    setStatus('当前目录：' + data.path);
    return data;
  } catch (e) { toast('读取目录失败：' + e.message, 'err'); }
}
function selectRow(tr) {
  $$('#file-body tr').forEach((r) => r.classList.remove('selected'));
  tr.classList.add('selected');
  S.selected = S.items.find((i) => i.path === tr.dataset.path) || null;
}
function selectRowByPath(path) {
  const tr = $$('#file-body tr').find((r) => r.dataset.path === path);
  if (tr) selectRow(tr);
}
function sortList(key) {
  S.sortRev = S.sortKey === key ? !S.sortRev : false;
  S.sortKey = key;
  const items = S.items.slice().sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
    let r;
    if (key === 'size' || key === 'mtime_raw') r = (a[key] || 0) - (b[key] || 0);
    else r = String(a[key] || '').localeCompare(String(b[key] || ''), 'zh-CN');
    return S.sortRev ? -r : r;
  });
  S.items = items;
  renderList(items);
}
async function setCwd(path) {
  if (!await confirmDiscard()) return;
  S.selected = null; S.file = null; S.dirty = false;
  await loadList(path);
  showWelcome();
  highlightTree(S.cwd);
}

/* ------------------------------------------------------------ 视图切换 */

function showView(name) {
  S.view = name;
  const titles = { welcome: '预览 / 编辑', 'editor-view': '文本编辑', 'image-view': '图片查看', 'pdf-view': 'PDF 文档', 'readonly-view': '只读预览' };
  ['welcome', 'editor-view', 'image-view', 'pdf-view', 'readonly-view'].forEach((id) => {
    $('#' + id).classList.toggle('hidden', id !== name);
  });
  $('#view-title').textContent = titles[name] || '预览 / 编辑';
  renderTools(name);
}
function showWelcome() {
  showView('welcome');
  updateTitle();
  setDetail('');
}
function renderTools(name) {
  const bar = $('#view-tools');
  const admin = isAdmin();
  const b = (id, label, title, primary) =>
    (!admin && ADMIN_ACTS.has(id) ? '' :
      '<button data-act="' + id + '"' + (primary ? ' class="primary"' : '') +
      ' title="' + esc(title || label) + '">' + esc(label) + '</button>');
  const readOnlyTag = admin ? '' : '<span class="muted">只读预览 · </span>';
  let html = '';
  if (name === 'editor-view') {
    html = readOnlyTag + b('save', '保存', 'Ctrl+S', true) + b('saveas', '另存为') + b('reload', '重新载入') + b('find', '查找', 'Ctrl+F') +
      '<span class="sep"></span><label>编码</label><select id="enc-sel">' +
      ENCODINGS.map((e) => '<option value="' + e + '"' + (e === S.encoding ? ' selected' : '') + '>' + e + '</option>').join('') +
      '</select>' + b('fontdec', 'A−') + b('fontinc', 'A+') +
      b('download', '下载') + b('system', '系统打开') + b('info', '文件信息');
  } else if (name === 'image-view') {
    html = b('prev', '上一张') + b('zoomout', '缩小') + b('fit', '适应窗口') + b('zoomin', '放大') +
      b('actual', '1:1') + b('rotate', '旋转') + b('next', '下一张') +
      b('download', '下载') + b('system', '系统打开') + b('info', '文件信息');
  } else if (name === 'pdf-view') {
    html = b('pdftoggle', S.pdfMode === 'viewer' ? '查看提取文本' : '回到 PDF 阅读器') +
      b('export', '导出文本') + b('download', '下载') + b('system', '系统打开') + b('info', '文件信息');
  } else if (name === 'readonly-view') {
    html = b('export', '导出预览文本') + b('download', '下载') + b('system', '系统打开') +
      b('reload', '重新载入') + b('info', '文件信息');
  } else {
    html = admin
      ? '<span class="muted">单击文件查看 / 编辑 · 双击目录进入 · 双击文件用系统默认程序打开</span>'
      : '<span class="muted">游客预览模式 · 可浏览目录、查看与下载文件，编辑 / 上传 / 删除需登录管理员</span>';
  }
  bar.innerHTML = html;
  bar.querySelectorAll('[data-act]').forEach((btn) => btn.addEventListener('click', () => toolAction(btn.dataset.act)));
  const enc = $('#enc-sel');
  if (enc) enc.addEventListener('change', () => changeEncoding(enc.value));
}
async function toolAction(act) {
  try {
    switch (act) {
      case 'save': await save(); break;
      case 'saveas': openSaveAs(false); break;
      case 'export': openSaveAs(true); break;
      case 'reload': await reloadFile(); break;
      case 'find': openFind(); break;
      case 'fontdec': case 'fontinc': changeFontSize(act === 'fontinc' ? 1 : -1); break;
      case 'download': downloadCurrent(); break;
      case 'system': await openSystemPath(currentPath()); break;
      case 'info': showFileInfo(); break;
      case 'prev': stepImage(-1); break;
      case 'next': stepImage(1); break;
      case 'zoomout': zoomImage(0.8); break;
      case 'zoomin': zoomImage(1.25); break;
      case 'fit': fitImage(); break;
      case 'actual': actualSize(); break;
      case 'rotate': rotateImage(); break;
      case 'pdftoggle': togglePdf(); break;
    }
  } catch (e) { toast(e.message, 'err'); }
}
const currentPath = () => (S.file && S.file.path) || (S.selected && S.selected.path) || '';

/* ------------------------------------------------------------ 打开文件 */

async function loadFile(item, skipConfirm = false) {
  if (!item) return;
  if (!skipConfirm && !await confirmDiscard()) return;
  setStatus('正在打开：' + item.name);
  try {
    const f = await GET('/api/file?path=' + q(item.path));
    S.file = f; S.selected = item; S.dirty = false;
    S.encoding = f.encoding || 'UTF-8';
    S.zoom = 1; S.panX = 0; S.panY = 0; S.rot = 0;
    if (f.category === 'text') {
      $('#editor').value = f.content;
      showView('editor-view');
      updateGutter();
      $('#gutter').scrollTop = 0;
      $('#editor').scrollTop = 0;
      setDetail(f.encoding + ' · ' + (f.truncated ? '仅载入前 12 MB' : f.size_h || '') );
      if (f.truncated) toast('文件过大，仅载入前 12 MB', 'err');
    } else if (f.category === 'image') {
      showImage(f);
    } else if (f.category === 'pdf') {
      S.pdfMode = 'viewer';
      $('#pdf-frame').classList.remove('hidden');
      $('#pdf-text').classList.add('hidden');
      $('#pdf-text').textContent = f.content || '';
      $('#pdf-frame').src = withSid(f.pdf_url);
      showView('pdf-view');
      setDetail(f.size_h + ' · 浏览器内置 PDF 阅读器');
    } else {
      $('#readonly').textContent = f.content || '';
      showView('readonly-view');
      setDetail(f.size_h + ' · ' + (f.label || ''));
    }
    updateTitle();
    setStatus('已打开：' + f.name);
  } catch (e) {
    toast('打开失败：' + e.message, 'err');
    setStatus('打开失败');
  }
}
async function confirmDiscard() {
  if (!S.dirty) return true;
  return new Promise((resolve) => {
    openModal('有未保存的修改', '<p>「' + esc(S.file ? S.file.name : '') + '」尚未保存，确定要离开吗？</p>', [
      { label: '取消', onClick: () => { resolve(false); } },
      { label: '保存并离开', primary: true, onClick: async () => {
        try { await save(); resolve(true); } catch (e) { resolve(false); }
      } },
      { label: '放弃修改', danger: true, onClick: () => { resolve(true); } }
    ]);
  });
}
function updateGutter() {
  const lines = $('#editor').value.split('\n').length;
  if (lines > 8000) { $('#gutter').textContent = '…'; return; }
  let s = '';
  for (let i = 1; i <= lines; i++) s += i + '\n';
  $('#gutter').textContent = s;
}

/* ------------------------------------------------------------ 编辑与保存 */

async function save() {
  if (!requireAdmin()) return;
  if (!S.file || S.file.category !== 'text') return toast('当前文件不可编辑', 'err');
  const r = await POST('/api/save', {
    path: S.file.path, content: $('#editor').value,
    encoding: S.encoding, mtime: S.file.mtime_raw
  });
  S.file.mtime_raw = r.mtime;
  S.dirty = false;
  updateTitle();
  toast('已保存：' + S.file.name);
  setStatus('已保存：' + S.file.path);
}
async function reloadFile() {
  if (!S.file) return;
  if (!await confirmDiscard()) return;
  S.dirty = false;
  await loadFile(S.file, true);
}
async function changeEncoding(enc) {
  if (!S.file) return;
  if (!await confirmDiscard()) { renderTools('editor-view'); return; }
  try {
    const f = await GET('/api/file?path=' + q(S.file.path) + '&encoding=' + q(enc));
    S.file = f; S.encoding = enc; S.dirty = false;
    $('#editor').value = f.content;
    updateGutter(); updateTitle();
    renderTools('editor-view');
    toast('已按 ' + enc + ' 重新解码');
  } catch (e) { toast(e.message, 'err'); renderTools('editor-view'); }
}
function changeFontSize(delta) {
  if (!S.cfg) return;
  S.cfg.font_size = Math.min(24, Math.max(10, Number(S.cfg.font_size || 13) + delta));
  applyConfig();
  if (isAdmin()) POST('/api/config', { font_size: S.cfg.font_size }).catch(() => {});
}
function openFind() {
  openModal('查找文本',
    '<div class="field"><label>关键字</label><input id="m-find" placeholder="输入要查找的内容"></div>' +
    '<p class="hint">按回车或点「查找下一个」继续定位</p>', [
      { label: '关闭' },
      { label: '查找下一个', primary: true, keepOpen: true, onClick: () => findNext($('#m-find').value) }
    ]);
  const inp = $('#m-find');
  inp.focus();
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); findNext(inp.value); }
  });
}
function findNext(kw) {
  if (!kw) return toast('请输入关键字', 'err');
  const ta = $('#editor');
  const text = ta.value;
  let idx = text.toLowerCase().indexOf(kw.toLowerCase(), S.findIndex);
  if (idx < 0) { S.findIndex = 0; idx = text.toLowerCase().indexOf(kw.toLowerCase(), 0); }
  if (idx < 0) return toast('没有找到「' + kw + '」', 'err');
  S.findIndex = idx + kw.length;
  ta.focus();
  ta.setSelectionRange(idx, idx + kw.length);
  const line = text.slice(0, idx).split('\n').length - 1;
  const lh = parseFloat(getComputedStyle(ta).lineHeight) || 20;
  ta.scrollTop = Math.max(0, (line - 4) * lh);
  $('#gutter').scrollTop = ta.scrollTop;
}
function openSaveAs(fromPreview) {
  if (!requireAdmin()) return;
  const isText = S.view === 'editor-view' && !fromPreview;
  const content = isText ? $('#editor').value : (S.file ? S.file.content : '');
  const defName = isText
    ? (S.file ? S.file.name : '新建.txt')
    : ((S.file ? S.file.name : '预览') + '_预览.txt');
  openModal('另存为 / 导出文本',
    '<div class="field"><label>目标文件名（或绝对路径）</label><input id="m-input" value="' + esc(defName) + '"></div>' +
    '<div class="field"><label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="m-overwrite"> 覆盖同名文件</label></div>' +
    '<p class="hint">保存在当前存储空间内：' + esc(S.cwd) + '</p>', [
      { label: '取消' },
      { label: '保存', primary: true, onClick: async () => {
        const target = $('#m-input').value.trim();
        if (!target) { toast('请输入文件名', 'err'); return false; }
        try {
          const r = await POST('/api/saveas', {
            dir: S.cwd, target, content,
            encoding: isText ? S.encoding : 'UTF-8',
            overwrite: $('#m-overwrite').checked
          });
          toast('已保存：' + r.name);
          await loadList(S.cwd);
        } catch (e) { toast(e.message, 'err'); return false; }
      } }
    ]);
  $('#m-input').focus();
}
function downloadCurrent() {
  const p = currentPath();
  if (!p) return toast('请先选择文件', 'err');
  const a = document.createElement('a');
  a.href = withSid('/api/download?path=' + q(p));
  a.download = '';
  document.body.appendChild(a); a.click(); a.remove();
}
async function openSystemPath(path) {
  if (!requireAdmin()) return;
  if (!path) return toast('请先选择文件', 'err');
  try { await POST('/api/open-system', { path }); toast('已调用系统默认程序'); }
  catch (e) { toast(e.message, 'err'); }
}
function showFileInfo() {
  if (!S.file) return toast('请先选择文件', 'err');
  openModal('文件信息', '<pre class="info-pre">' + esc(S.file.info || '') + '</pre>',
    [{ label: '关闭', primary: true }]);
}

/* ------------------------------------------------------------ 图片 */

function showImage(f) {
  const img = $('#image');
  img.onload = () => {
    S.imgW = img.naturalWidth; S.imgH = img.naturalHeight;
    S.zoom = 1; S.panX = 0; S.panY = 0; S.rot = 0;
    applyTransform();
    setDetail(f.image_meta || (S.imgW + ' × ' + S.imgH));
  };
  img.onerror = () => toast('图片加载失败', 'err');
  img.src = withSid(f.image_url);
  showView('image-view');
}
function fitScale() {
  const stage = $('#image-stage');
  if (!S.imgW || !S.imgH) return 1;
  return Math.min((stage.clientWidth - 24) / S.imgW, (stage.clientHeight - 24) / S.imgH, 4);
}
function applyTransform() {
  if (!S.imgW) return;
  const scale = fitScale() * S.zoom;
  $('#image').style.transform =
    'translate(' + S.panX + 'px,' + S.panY + 'px) scale(' + scale + ') rotate(' + S.rot + 'deg)';
  setDetail(S.imgW + ' × ' + S.imgH + ' · ' + Math.round(scale * 100) + '% · 旋转 ' + S.rot + '°');
}
const fitImage = () => { S.zoom = 1; S.panX = 0; S.panY = 0; applyTransform(); };
const zoomImage = (f) => { S.zoom = Math.min(20, Math.max(0.05, S.zoom * f)); applyTransform(); };
const actualSize = () => { S.zoom = 1 / fitScale(); S.panX = 0; S.panY = 0; applyTransform(); };
const rotateImage = () => { S.rot = (S.rot + 90) % 360; applyTransform(); };
function stepImage(delta) {
  if (!S.imageList.length) return toast('当前目录没有图片', 'err');
  const idx = S.imageList.findIndex((i) => i.path === currentPath());
  const next = S.imageList[(idx + delta + S.imageList.length) % S.imageList.length];
  if (!next) return;
  selectRowByPath(next.path);
  loadFile(next);
}
function togglePdf() {
  S.pdfMode = S.pdfMode === 'viewer' ? 'text' : 'viewer';
  $('#pdf-frame').classList.toggle('hidden', S.pdfMode !== 'viewer');
  $('#pdf-text').classList.toggle('hidden', S.pdfMode !== 'text');
  renderTools('pdf-view');
}

/* ------------------------------------------------------------ 文件操作 */

function openCreate(kind) {
  if (!requireAdmin()) return;
  const label = kind === 'folder' ? '新建文件夹' : '新建文件';
  openModal(label,
    '<div class="field"><label>名称</label><input id="m-input" placeholder="' +
    (kind === 'folder' ? '新文件夹' : '例如 笔记.txt') + '"></div>' +
    '<p class="hint">创建位置：' + esc(S.cwd) + '</p>', [
      { label: '取消' },
      { label: '创建', primary: true, onClick: async () => {
        const name = $('#m-input').value.trim();
        if (!name) { toast('请输入名称', 'err'); return false; }
        try {
          await POST('/api/create', { dir: S.cwd, name, kind });
          toast('已创建：' + name);
          await loadList(S.cwd);
        } catch (e) { toast(e.message, 'err'); return false; }
      } }
    ]);
  $('#m-input').focus();
}
function openRename() {
  if (!requireAdmin()) return;
  const it = S.selected;
  if (!it) return toast('请先选择文件或文件夹', 'err');
  openModal('重命名',
    '<div class="field"><label>新名称</label><input id="m-input" value="' + esc(it.name) + '"></div>' +
    '<p class="hint">' + esc(it.path) + '</p>', [
      { label: '取消' },
      { label: '确定', primary: true, onClick: async () => {
        const name = $('#m-input').value.trim();
        if (!name) { toast('请输入名称', 'err'); return false; }
        try {
          await POST('/api/rename', { path: it.path, name });
          toast('已重命名为：' + name);
          S.selected = null; S.file = null; S.dirty = false;
          await loadList(S.cwd); showWelcome(); await loadTree();
        } catch (e) { toast(e.message, 'err'); return false; }
      } }
    ]);
  $('#m-input').focus();
}
function openDelete() {
  if (!requireAdmin()) return;
  const it = S.selected;
  if (!it) return toast('请先选择文件或文件夹', 'err');
  openModal('删除确认',
    '<p>确定删除「' + esc(it.name) + '」吗？</p>' +
    '<p class="hint">' + esc(it.path) + '</p>' +
    '<p class="warn">永久删除不可恢复，建议优先使用「移入回收站」。</p>', [
      { label: '取消' },
      { label: '永久删除', danger: true, onClick: () => doDelete(true) },
      { label: '移入回收站', primary: true, onClick: () => doDelete(false) }
    ]);
}
async function doDelete(permanent) {
  const it = S.selected;
  if (!it) return false;
  try {
    const r = await POST('/api/delete', { path: it.path, permanent });
    toast(r.message);
    S.selected = null; S.file = null; S.dirty = false;
    await loadList(S.cwd); showWelcome(); await loadTree();
  } catch (e) { toast(e.message, 'err'); return false; }
}
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}
async function uploadFiles(files) {
  if (!requireAdmin()) return;
  let ok = 0;
  for (const f of files) {
    if (f.size > 60 * 1024 * 1024) { toast(f.name + ' 超过 60 MB，已跳过', 'err'); continue; }
    try {
      const data = await fileToBase64(f);
      await POST('/api/upload', { dir: S.cwd, name: f.name, data });
      ok++;
    } catch (e) { toast(f.name + ' 上传失败：' + e.message, 'err'); }
  }
  if (ok) { toast('已上传 ' + ok + ' 个文件'); await loadList(S.cwd); }
}
async function doSearch() {
  const kw = $('#search').value.trim();
  if (!kw) return clearSearch();
  try {
    const r = await GET('/api/search?q=' + q(kw));
    S.items = r.items;
    S.searchMode = true;
    S.imageList = r.items.filter((i) => i.category === 'image');
    $('#list-title').textContent = '搜索结果：' + kw;
    renderList(r.items);
    setStatus('搜索「' + kw + '」找到 ' + r.items.length + ' 个结果' + (r.truncated ? '（已达上限 400）' : ''));
  } catch (e) { toast('搜索失败：' + e.message, 'err'); }
}
function clearSearch() {
  $('#search').value = '';
  return loadList(S.cwd);
}

/* ------------------------------------------------------------ 存储空间选择 */

let browsePath = '';
let browseParent = '';
function openRootPicker() {
  if (!requireAdmin()) return;
  const html =
    '<div class="field"><label>直接输入文件夹绝对路径</label>' +
    '<div class="inline"><input id="root-input" value="' + esc(S.root) + '" placeholder="例如 F:\\工作资料">' +
    '<button id="root-apply">应用</button></div></div>' +
    '<div class="field"><label>快捷位置</label><div id="root-shortcuts" class="chips"></div></div>' +
    '<div class="field"><label>浏览服务器目录</label>' +
    '<div class="inline"><button id="root-up">上级</button><span id="root-cur" class="path-chip"></span></div>' +
    '<div id="root-dirs" class="dir-list"></div></div>' +
    '<p class="hint">服务监听 0.0.0.0:1111，局域网内其他设备也能访问；所有文件读写都限制在你选择的目录内。</p>';
  openModal('选择存储空间地址', html, [
    { label: '取消' },
    { label: '选为存储空间', primary: true, onClick: async () => {
      const typed = $('#root-input') ? $('#root-input').value.trim() : '';
      return setRoot(browsePath || typed);
    } }
  ]);
  $('#root-shortcuts').innerHTML = S.drives
    .map((d) => '<span class="chip" data-path="' + esc(d.path) + '">' + esc(d.name) + '</span>').join('');
  $('#root-shortcuts').addEventListener('click', (e) => {
    const c = e.target.closest('.chip');
    if (c) refreshBrowse(c.dataset.path);
  });
  $('#root-dirs').addEventListener('click', (e) => {
    const d = e.target.closest('.dir-item');
    if (d) refreshBrowse(d.dataset.path);
  });
  $('#root-up').onclick = () => refreshBrowse(browseParent);
  $('#root-apply').onclick = async () => {
    const typed = $('#root-input').value.trim();
    if (await setRoot(typed)) closeModal();
  };
  refreshBrowse(S.root);
}
async function refreshBrowse(path) {
  try {
    const r = await GET('/api/browse?path=' + q(path || ''));
    browsePath = r.path;
    browseParent = r.parent;
    $('#root-cur').textContent = r.path || '（选择一个位置）';
    $('#root-input').value = r.path || $('#root-input').value;
    $('#root-dirs').innerHTML = r.items.length
      ? r.items.map((d) => '<div class="dir-item" data-path="' + esc(d.path) + '">▣ ' + esc(d.name) + '</div>').join('')
      : '<div class="tree-empty">没有子目录</div>';
  } catch (e) { toast('浏览失败：' + e.message, 'err'); }
}
async function setRoot(path) {
  if (!path) { toast('请选择或输入目录', 'err'); return false; }
  try {
    const r = await POST('/api/root', { path });
    S.root = r.root; S.cwd = r.root;
    S.selected = null; S.file = null; S.dirty = false;
    await loadTree(); await loadList(r.root); showWelcome();
    toast('存储空间已切换为：' + r.root);
    return true;
  } catch (e) { toast(e.message, 'err'); return false; }
}

/* ------------------------------------------------------------ 外观设置 */

function openSettings() {
  if (!requireAdmin()) return;
  const c = S.cfg;
  if (!c) return;
  const backup = JSON.parse(JSON.stringify(c));
  const colorField = (key, label) =>
    '<div class="field"><label>' + label + '</label><input type="color" data-color="' + key + '" value="' + esc(c[key]) + '"></div>';
  const modeOpts = [['fit', '完整显示（适应）'], ['stretch', '拉伸铺满'], ['center', '居中裁剪铺满'], ['tile', '平铺']];
  const themeOpts = [['dark', '深色'], ['light', '浅色'], ['eye', '护眼绿'], ['warm', '暖棕'], ['custom', '自定义']];
  const html =
    '<div class="settings-grid">' +
    '<div class="field"><label>配色预设</label><select id="set-theme">' +
    themeOpts.map(([v, l]) => '<option value="' + v + '"' + (c.theme === v ? ' selected' : '') + '>' + l + '</option>').join('') +
    '</select></div>' +
    '<div class="field"><label>编辑器字号</label><input type="number" id="set-font" min="10" max="24" value="' + esc(c.font_size) + '"></div>' +
    colorField('bg', '页面底色') + colorField('panel', '面板底色') +
    colorField('fg', '文字颜色') + colorField('accent', '强调色') + colorField('select', '选中色') +
    '<div class="field"><label>背景图片</label><div class="inline">' +
    '<input type="file" id="set-bgfile" accept="image/*"><button id="set-clearbg">清除</button></div>' +
    '<span class="hint" id="bg-name">' + esc(c.bg_image ? c.bg_image.split(/[\\/]/).pop() : '未设置') + '</span></div>' +
    '<div class="field"><label>显示方式</label><select id="set-bgmode">' +
    modeOpts.map(([v, l]) => '<option value="' + v + '"' + (c.bg_mode === v ? ' selected' : '') + '>' + l + '</option>').join('') +
    '</select></div>' +
    '<div class="field wide"><label>背景淡化：<span id="set-fade-val">' + Math.round((1 - Number(c.bg_fade || 0)) * 100) + '%</span> 不透明度</label>' +
    '<input type="range" id="set-fade" min="0" max="95" value="' + Math.round(Number(c.bg_fade || 0) * 100) + '"></div>' +
    '<div class="field"><label>服务端口（重启后生效）</label><input type="number" id="set-port" min="1" max="65535" value="' + esc(c.port) + '"></div>' +
    '<div class="field"><label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="set-autoopen"' +
    (c.auto_open ? ' checked' : '') + '> 启动时自动打开浏览器</label>' +
    '<p class="hint">当前为管理员模式；其他访客可用游客身份只读预览。</p></div>' +
    '</div>' +
    '<p class="hint">端口默认 1111（若被占用会自动顺延）。其他设备访问 http://本机IP:1111 ，' +
    '打开页面后可用管理员账号登录，或选择「游客预览」。若连不上，请在 Windows 防火墙放行该端口。</p>';
  openModal('外观与运行设置', html, [
    { label: '取消', onClick: async () => {
      Object.assign(S.cfg, backup);
      const restore = {};
      ['theme', 'bg', 'panel', 'fg', 'accent', 'select', 'bg_image', 'bg_mode', 'bg_fade', 'font_size', 'port', 'auto_open']
        .forEach((k) => { restore[k] = backup[k]; });
      try { const r = await POST('/api/config', restore); S.cfg = r.config; } catch (e) { /* ignore */ }
      applyConfig();
    } },
    { label: '保存设置', primary: true, onClick: async () => {
      const payload = {
        theme: $('#set-theme').value,
        bg_mode: $('#set-bgmode').value,
        bg_fade: Number($('#set-fade').value) / 100,
        font_size: Number($('#set-font').value),
        port: Number($('#set-port').value),
        auto_open: $('#set-autoopen').checked
      };
      $$('#modal-body [data-color]').forEach((i) => { payload[i.dataset.color] = i.value; });
      try {
        const r = await POST('/api/config', payload);
        S.cfg = r.config; applyConfig();
        toast('设置已保存' + (payload.port !== S.port ? '（端口重启后生效）' : ''));
      } catch (e) { toast('保存失败：' + e.message, 'err'); return false; }
    } },
    { label: '退出服务', danger: true, keepOpen: true, onClick: async () => {
      if (!window.confirm('确定停止 FileStudio 服务并退出吗？')) return false;
      try { await POST('/api/shutdown', {}); } catch (e) { /* 连接即将关闭 */ }
      toast('正在停止服务…');
    } }
  ]);
  $('#set-theme').addEventListener('change', (e) => {
    const t = THEMES[e.target.value];
    if (!t) return;
    Object.assign(S.cfg, t, { theme: e.target.value });
    $$('#modal-body [data-color]').forEach((i) => { i.value = S.cfg[i.dataset.color]; });
    applyConfig();
  });
  $$('#modal-body [data-color]').forEach((i) => i.addEventListener('input', (e) => {
    S.cfg[i.dataset.color] = e.target.value;
    S.cfg.theme = 'custom';
    $('#set-theme').value = 'custom';
    applyConfig();
  }));
  $('#set-fade').addEventListener('input', (e) => {
    S.cfg.bg_fade = Number(e.target.value) / 100;
    $('#set-fade-val').textContent = Math.round((1 - S.cfg.bg_fade) * 100) + '%';
    applyConfig();
  });
  $('#set-font').addEventListener('input', (e) => { S.cfg.font_size = Number(e.target.value); applyConfig(); });
  $('#set-bgmode').addEventListener('change', (e) => { S.cfg.bg_mode = e.target.value; applyConfig(); });
  $('#set-bgfile').addEventListener('change', async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const data = await fileToBase64(f);
      const r = await POST('/api/upload-bg', { name: f.name, data });
      S.cfg.bg_image = r.path;
      $('#bg-name').textContent = f.name + '（已上传）';
      applyConfig();
      toast('背景图已应用');
    } catch (err) { toast('上传失败：' + err.message, 'err'); }
  });
  $('#set-clearbg').addEventListener('click', async () => {
    S.cfg.bg_image = '';
    try { const r = await POST('/api/config', { bg_image: '' }); S.cfg = r.config; } catch (e) { /* ignore */ }
    $('#bg-name').textContent = '未设置';
    applyConfig();
  });
}

/* ------------------------------------------------------------ 事件绑定 */

function bindStatic() {
  $('#btn-root').onclick = openRootPicker;
  $('#btn-up').onclick = async () => {
    if (S.parent) await setCwd(S.parent);
    else toast('已经是存储空间根目录', 'err');
  };
  $('#btn-refresh').onclick = async () => {
    if (S.searchMode) await doSearch(); else await loadList(S.cwd);
  };
  $('#btn-explorer').onclick = async () => {
    try { await POST('/api/open-explorer', { path: currentPath() || S.cwd }); }
    catch (e) { toast(e.message, 'err'); }
  };
  $('#btn-settings').onclick = openSettings;
  $('#btn-newfile').onclick = () => openCreate('file');
  $('#btn-newfolder').onclick = () => openCreate('folder');
  $('#btn-upload').onclick = () => $('#file-input').click();
  $('#file-input').addEventListener('change', (e) => {
    uploadFiles(Array.from(e.target.files));
    e.target.value = '';
  });
  $('#btn-rename').onclick = openRename;
  $('#btn-delete').onclick = openDelete;
  $('#btn-search').onclick = doSearch;
  $('#btn-clear').onclick = clearSearch;
  $('#search').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });

  $$('th[data-sort]').forEach((th) => th.addEventListener('click', () => sortList(th.dataset.sort)));
  $('#file-body').addEventListener('click', async (e) => {
    const tr = e.target.closest('tr');
    if (!tr || !tr.dataset.path) return;
    selectRow(tr);
    const it = S.selected;
    if (!it) return;
    if (it.is_dir) setStatus('已选中文件夹：' + it.name + '（双击进入）');
    else await loadFile(it);
  });
  $('#file-body').addEventListener('dblclick', async (e) => {
    const tr = e.target.closest('tr');
    if (!tr || !tr.dataset.path) return;
    const it = S.items.find((i) => i.path === tr.dataset.path);
    if (!it) return;
    if (it.is_dir) await setCwd(it.path);
    else if (isAdmin()) await openSystemPath(it.path);
    else toast('游客请用工具栏「下载」保存后查看，或登录管理员', 'err');
  });

  const editor = $('#editor');
  editor.addEventListener('input', () => { S.dirty = true; updateGutter(); updateTitle(); });
  editor.addEventListener('scroll', () => { $('#gutter').scrollTop = editor.scrollTop; });
  editor.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      editor.setRangeText('    ', editor.selectionStart, editor.selectionEnd, 'end');
      editor.dispatchEvent(new Event('input'));
    }
  });

  const stage = $('#image-stage');
  stage.addEventListener('wheel', (e) => {
    if (S.view !== 'image-view') return;
    e.preventDefault();
    zoomImage(e.deltaY < 0 ? 1.12 : 0.89);
  }, { passive: false });
  let drag = null;
  stage.addEventListener('mousedown', (e) => {
    if (S.view !== 'image-view') return;
    drag = { x: e.clientX, y: e.clientY, px: S.panX, py: S.panY };
    stage.style.cursor = 'grabbing';
  });
  window.addEventListener('mousemove', (e) => {
    if (!drag) return;
    S.panX = drag.px + e.clientX - drag.x;
    S.panY = drag.py + e.clientY - drag.y;
    applyTransform();
  });
  window.addEventListener('mouseup', () => { drag = null; stage.style.cursor = 'grab'; });
  window.addEventListener('resize', () => { if (S.view === 'image-view') applyTransform(); });

  $('#modal-x').onclick = closeModal;
  $('#modal-mask').addEventListener('click', (e) => { if (e.target.id === 'modal-mask') closeModal(); });
  $('#login-ok').onclick = tryLogin;
  ['#login-user', '#login-pass'].forEach((sel) => {
    $(sel).addEventListener('keydown', (e) => { if (e.key === 'Enter') tryLogin(); });
  });
  $('#login-guest').onclick = enterGuest;
  $('#btn-auth').onclick = () => { if (isAdmin()) logout(); else showLogin(''); };

  document.addEventListener('keydown', (e) => {
    const el = e.target;
    const typing = el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT');
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); if (S.view === 'editor-view' && isAdmin()) save(); }
    else if (mod && e.key.toLowerCase() === 'o') { e.preventDefault(); if (isAdmin()) openRootPicker(); }
    else if (mod && e.key.toLowerCase() === 'n') { e.preventDefault(); if (isAdmin()) openCreate('file'); }
    else if (mod && e.key.toLowerCase() === 'f') {
      e.preventDefault();
      if (S.view === 'editor-view') openFind(); else $('#search').focus();
    }
    else if (e.key === 'F5') { e.preventDefault(); S.searchMode ? doSearch() : loadList(S.cwd); }
    else if (e.key === 'F2' && !typing && isAdmin()) { e.preventDefault(); openRename(); }
    else if (e.key === 'Delete' && !typing && isAdmin()) { e.preventDefault(); openDelete(); }
    else if (e.key === 'Escape') closeModal();
    else if (e.key === 'ArrowLeft' && S.view === 'image-view' && !typing) stepImage(-1);
    else if (e.key === 'ArrowRight' && S.view === 'image-view' && !typing) stepImage(1);
  });
  window.addEventListener('beforeunload', (e) => {
    if (S.dirty) { e.preventDefault(); e.returnValue = ''; }
  });
}
async function tryLogin() {
  const username = $('#login-user').value.trim();
  const password = $('#login-pass').value;
  if (!username || !password) { $('#login-msg').textContent = '请输入账号和密码'; return; }
  const btn = $('#login-ok');
  btn.disabled = true;
  btn.textContent = '正在登录…';
  $('#login-msg').textContent = '';
  try {
    const r = await POST('/api/login', { username, password });
    localStorage.setItem('fs_sid', r.sid);
    localStorage.removeItem('fs_guest');
    S.sid = r.sid;
    S.role = 'admin';
    hideLogin();
    const st = await GET('/api/state');
    await applyState(st);
    toast('已以管理员身份登录');
  } catch (e) {
    $('#login-msg').textContent = e.message || '登录失败，请重试';
    $('#login-pass').value = '';
    $('#login-pass').focus();
  } finally {
    btn.disabled = false;
    btn.textContent = '管理员登录';
  }
}
async function enterGuest() {
  if (!S.guestEnabled) { toast('管理员已关闭游客预览', 'err'); return; }
  try {
    try { await POST('/api/logout', {}); } catch (e) { /* 清除可能残留的管理员会话 */ }
    await POST('/api/guest', {});
    localStorage.setItem('fs_guest', '1');
    localStorage.removeItem('fs_sid');
    S.sid = '';
    S.role = 'guest';
    hideLogin();
    const st = await GET('/api/state');
    await applyState(st);
    toast('已进入游客预览（只读）');
  } catch (e) { toast(e.message, 'err'); }
}
async function logout() {
  if (S.dirty && !window.confirm('有未保存的修改，确定退出登录吗？')) return;
  try { await POST('/api/logout', {}); } catch (e) { /* 会话可能已过期 */ }
  localStorage.removeItem('fs_sid');
  localStorage.removeItem('fs_guest');
  S.sid = ''; S.role = 'guest'; S.dirty = false;
  applyRole();
  showLogin('已退出登录，可重新登录或选择游客预览');
}
async function applyState(st) {
  S.cfg = st.config;
  S.role = st.role === 'admin' ? 'admin' : 'guest';
  S.guestEnabled = !!st.guest_enabled;
  S.root = st.root;
  S.cwd = st.root;
  S.drives = st.drives;
  S.port = st.port;
  applyConfig();
  applyRole();
  await loadTree();
  await loadList(S.root);
  showWelcome();
  setStatus((isAdmin() ? '管理员' : '游客预览（只读）') + ' · 端口 ' + st.port + ' · 局域网可访问' +
    (st.caps.pillow ? ' · 图片增强已启用' : '') + (st.caps.pypdf ? ' · PDF 文本提取已启用' : ''));
}
async function init() {
  const params = new URLSearchParams(location.search);
  const urlSid = params.get('sid') || params.get('token');  // 兼容旧版本链接
  if (urlSid) {
    localStorage.setItem('fs_sid', urlSid);
    localStorage.removeItem('fs_guest');
    history.replaceState({}, '', location.pathname);
  }
  S.sid = localStorage.getItem('fs_sid') || '';
  bindStatic();
  try {
    const st = await GET('/api/state');
    S.cfg = st.config;
    S.guestEnabled = !!st.guest_enabled;
    const rememberedGuest = localStorage.getItem('fs_guest') === '1';
    if (st.role === 'admin' || (st.role === 'guest' && st.guest_enabled && rememberedGuest)) {
      hideLogin();
      await applyState(st);
    } else {
      S.role = 'guest';
      applyConfig();
      applyRole();
      showLogin(st.login_required ? '管理员已关闭游客预览，请登录后使用' : '');
    }
  } catch (e) {
    setStatus('连接服务失败：' + e.message);
    toast('连接服务失败：' + e.message, 'err');
  }
}
init();

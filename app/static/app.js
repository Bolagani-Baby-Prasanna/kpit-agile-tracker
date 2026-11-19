'use strict';
/* Sprintboard front-end: plain JS, no build step. All DOM is built with h() + textContent,
   so user-supplied text is never parsed as HTML (XSS-safe by construction). */

const STORY_STATUSES = [['backlog', 'Backlog'], ['in_progress', 'In progress'], ['done', 'Done']];
const TASK_STATUSES = [['todo', 'To do'], ['in_progress', 'In progress'], ['done', 'Done']];
const PRIORITIES = [['low', 'Low'], ['medium', 'Medium'], ['high', 'High']];

const S = {
  token: localStorage.getItem('sb_token'), me: null, users: [], projects: [], pid: null, project: null,
  open: new Set(), filter: { assignee: '', priority: '' }, notifs: { unread: 0, items: [] }, panel: false,
};
const $ = (sel) => document.querySelector(sel);
const today = () => new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD in local time
const userName = (id) => (S.users.find((u) => u.id === id) || {}).name || null;
const plural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;
const when = (iso) => new Date(iso + 'Z').toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });

/* ---------------------------------------------------------------- helpers */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style') el.style.cssText = v; // CSSOM is allowed by CSP; the style attribute is not
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  append(el, kids);
  return el;
}

/* append children, skipping false/null (so `cond && h(...)` works) and never parsing text as HTML */
function append(el, kids) {
  for (const c of kids.flat(Infinity)) {
    if (c === false || c == null) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

function toast(msg, bad) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'show' + (bad ? ' bad' : '');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.className = ''), 3500);
}

function fmtError(d) {
  if (!d) return '';
  if (typeof d.detail === 'string') return d.detail;
  if (Array.isArray(d.detail)) return d.detail.map((e) => `${(e.loc || []).slice(1).join('.')}: ${e.msg}`).join('; ');
  return '';
}

async function api(path, { method = 'GET', body } = {}) {
  const headers = {};
  if (body) headers['Content-Type'] = 'application/json';
  if (S.token) headers.Authorization = 'Bearer ' + S.token;
  const res = await fetch('/api' + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    if (res.status === 401 && S.token) logout();
    throw new Error(fmtError(data) || res.statusText);
  }
  return data;
}

/* run an action; show errors as a toast; always re-sync from the server afterwards */
const act = (fn) => async (...args) => {
  try { await fn(...args); } catch (e) { toast(e.message, true); }
  await reload();
};

/* ---------------------------------------------------------------- data loading */
async function loadAll() {
  [S.users, S.projects] = await Promise.all([api('/users'), api('/projects')]);
  if (!S.projects.some((p) => p.id === S.pid)) S.pid = S.projects.length ? S.projects[0].id : null;
  S.project = S.pid ? await api(`/projects/${S.pid}`) : null;
  await loadNotifs();
}

async function reload() {
  try {
    [S.users, S.projects] = await Promise.all([api('/users'), api('/projects')]);
    if (!S.projects.some((p) => p.id === S.pid)) S.pid = S.projects.length ? S.projects[0].id : null;
    S.project = S.pid ? await api(`/projects/${S.pid}`) : null;
  } catch (e) { toast(e.message, true); }
  render();
}

async function loadNotifs() {
  try { S.notifs = await api('/notifications'); } catch { /* ignore transient errors */ }
  updateBell();
}

function logout() {
  localStorage.removeItem('sb_token');
  Object.assign(S, { token: null, me: null, projects: [], project: null, pid: null, panel: false });
  render();
}

/* ---------------------------------------------------------------- render root */
function render() {
  $('#app').replaceChildren(S.me ? shell() : authView());
}

function authView() {
  let mode = 'login';
  const wrap = h('div', { class: 'auth' });
  const draw = () => {
    const err = h('p', { class: 'err', role: 'alert' });
    const form = h('form', {
      class: 'auth-card',
      onsubmit: async (e) => {
        e.preventDefault();
        const f = Object.fromEntries(new FormData(form));
        try {
          if (mode === 'register') await api('/auth/register', { method: 'POST', body: { name: f.name, email: f.email, password: f.password } });
          const r = await api('/auth/login', { method: 'POST', body: { email: f.email, password: f.password } });
          S.token = r.access_token; S.me = r.user;
          localStorage.setItem('sb_token', S.token);
          await loadAll();
          render();
        } catch (ex) { err.textContent = ex.message; }
      },
    },
      h('h1', {}, 'Sprintboard'),
      h('p', { class: 'sub' }, 'Plan projects, stories and tasks with your team.'),
      mode === 'register' && h('label', {}, 'Name', h('input', { name: 'name', required: true, maxlength: 80, autocomplete: 'name' })),
      h('label', {}, 'E-mail', h('input', { name: 'email', type: 'email', required: true, autocomplete: 'email' })),
      h('label', {}, 'Password', h('input', { name: 'password', type: 'password', required: true, minlength: 8, autocomplete: mode === 'login' ? 'current-password' : 'new-password' }),
        mode === 'register' && h('span', { class: 'hint' }, 'At least 8 characters')),
      err,
      h('button', { class: 'btn primary', type: 'submit' }, mode === 'login' ? 'Sign in' : 'Create account'),
      h('p', {}, mode === 'login' ? 'New here? ' : 'Already have an account? ',
        h('button', { type: 'button', class: 'link', onclick: () => { mode = mode === 'login' ? 'register' : 'login'; draw(); } },
          mode === 'login' ? 'Create an account' : 'Sign in')),
    );
    wrap.replaceChildren(form);
  };
  draw();
  return wrap;
}

function shell() {
  return h('div', { class: 'shell' }, sidebar(), h('main', { class: 'main' }, topbar(), S.project ? projectView() : emptyView()));
}

function emptyView() {
  return h('div', { class: 'empty' }, S.projects.length ? 'Select a project.' : 'No projects yet. Create your first one from the sidebar.');
}

/* ---------------------------------------------------------------- sidebar & top bar */
function sidebar() {
  return h('aside', { class: 'side' },
    h('div', { class: 'brand' }, 'Sprintboard'),
    h('button', { class: 'btn primary', onclick: newProjectForm }, 'New project'),
    h('nav', { class: 'plist', 'aria-label': 'Projects' }, S.projects.map((p) =>
      h('button', {
        class: 'pitem' + (p.id === S.pid ? ' active' : '') + (p.status === 'archived' ? ' archived' : ''),
        onclick: async () => { S.pid = p.id; await reload(); },
      },
        h('span', { class: 'pname' }, p.name),
        h('span', { class: 'pmeta' }, `${plural(p.story_count, 'story', 'stories')}, ${p.done_task_count}/${plural(p.task_count, 'task')} done`),
        h('span', { class: 'bar' }, h('i', { style: `width:${p.progress}%` })),
      ))),
  );
}

function topbar() {
  return h('div', { class: 'topbar' },
    h('span', { class: 'me' }, S.me.name),
    h('span', { class: 'bell' },
      h('button', { class: 'btn', 'aria-label': 'Notifications', onclick: async () => { S.panel = !S.panel; if (S.panel) await loadNotifs(); render(); } }, 'Notifications'),
      h('span', { class: 'badge', id: 'badge', hidden: !S.notifs.unread }, S.notifs.unread)),
    h('button', { class: 'btn', onclick: logout }, 'Sign out'),
    S.panel && notifPanel(),
  );
}

function notifPanel() {
  const items = S.notifs.items;
  return h('div', { class: 'panel', id: 'notif-panel' },
    h('header', {}, 'Notifications',
      h('span', { class: 'row' },
        h('button', { class: 'btn small', title: 'Queue the due-date reminder job now', onclick: async () => {
          try { await api('/jobs/due-scan', { method: 'POST' }); toast('Due-date scan queued - results appear in a moment'); setTimeout(loadNotifs, 2500); }
          catch (e) { toast(e.message, true); }
        } }, 'Check due dates'),
        h('button', { class: 'btn small', onclick: async () => { await api('/notifications/read-all', { method: 'POST' }); await loadNotifs(); } }, 'Mark all read'))),
    items.length ? items.map((n) =>
      h('div', { class: 'note' + (n.read ? '' : ' unread'), onclick: async () => { await api(`/notifications/${n.id}/read`, { method: 'POST' }); await loadNotifs(); } },
        h('span', {}, n.message), h('small', {}, when(n.created_at)))) : h('div', { class: 'empty' }, 'Nothing yet. Assign a task to a teammate to see one.'));
}

function updateBell() {
  const b = $('#badge');
  if (b) { b.textContent = S.notifs.unread; b.hidden = !S.notifs.unread; }
  const p = $('#notif-panel');
  if (p && S.panel) p.replaceWith(notifPanel());
}

/* ---------------------------------------------------------------- project view */
function projectView() {
  const p = S.project;
  const archived = p.status === 'archived';
  const isOwner = p.owner_id === S.me.id;
  const stories = p.stories.filter((s) =>
    (!S.filter.assignee || String(s.assignee_id) === S.filter.assignee) && (!S.filter.priority || s.priority === S.filter.priority));
  const people = [['', 'Anyone'], ...S.users.map((u) => [String(u.id), u.name])];

  return h('div', {},
    h('div', { class: 'phead' },
      h('h1', {}, p.name, archived && h('span', { class: 'chip archived' }, 'Archived')),
      p.description && h('p', { class: 'desc' }, p.description),
      h('div', { class: 'summary' },
        h('span', {}, plural(p.story_count, 'story', 'stories')), h('span', {}, `${p.done_task_count} of ${plural(p.task_count, 'task')} done`),
        h('span', { class: 'bar', title: p.progress + '%' }, h('i', { style: `width:${p.progress}%` })), h('span', {}, p.progress + '%')),
      h('div', { class: 'row' },
        h('button', { class: 'btn primary', disabled: archived, onclick: () => storyForm() }, 'Add story'),
        h('button', { class: 'btn', onclick: requestReport, title: 'Generated by a background job' }, 'Generate report'),
        h('button', { class: 'btn', onclick: editProjectForm }, 'Edit'),
        h('button', { class: 'btn', onclick: act(() => api(`/projects/${p.id}`, { method: 'PATCH', body: { status: archived ? 'active' : 'archived' } })) }, archived ? 'Unarchive' : 'Archive'),
        isOwner && h('button', { class: 'btn danger', onclick: act(async () => {
          if (!confirm(`Delete "${p.name}" and everything in it? This cannot be undone.`)) return;
          await api(`/projects/${p.id}`, { method: 'DELETE' }); S.pid = null;
        }) }, 'Delete')),
    ),
    h('div', { class: 'filters' },
      h('label', { class: 'row' }, 'Assignee', select(people, S.filter.assignee, (v) => { S.filter.assignee = v; render(); })),
      h('label', { class: 'row' }, 'Priority', select([['', 'Any'], ...PRIORITIES], S.filter.priority, (v) => { S.filter.priority = v; render(); }))),
    h('div', { class: 'board' }, STORY_STATUSES.map(([key, label]) => {
      const col = stories.filter((s) => s.status === key);
      return h('section', { class: 'col ' + key, 'aria-label': label },
        h('h2', {}, label, h('span', {}, col.length)),
        col.length ? col.map(storyCard) : h('p', { class: 'empty' }, 'No stories'));
    })),
  );
}

function select(options, value, onchange, attrs) {
  return h('select', { ...attrs, onchange: (e) => onchange(e.target.value) },
    options.map(([v, l]) => h('option', { value: v, selected: String(v) === String(value ?? '') }, l)));
}

function storyCard(s) {
  const archived = S.project.status === 'archived';
  const open = S.open.has(s.id);
  const pct = s.task_count ? Math.round((100 * s.done_task_count) / s.task_count) : 0;
  const who = userName(s.assignee_id);
  return h('article', { class: 'story' },
    h('div', { class: 'top' }, h('h3', {}, s.title), h('span', { class: 'chip pr-' + s.priority }, s.priority)),
    s.description && h('p', { class: 'desc' }, s.description),
    h('div', { class: 'meta' }, s.points != null && h('span', { class: 'chip' }, s.points + ' pts'), h('span', {}, who ? 'Assigned to ' + who : 'Unassigned')),
    h('div', { class: 'bar', title: `${s.done_task_count}/${s.task_count} tasks` }, h('i', { style: `width:${pct}%` })),
    h('div', { class: 'actions' },
      h('button', { class: 'btn small', 'aria-expanded': open, onclick: () => { open ? S.open.delete(s.id) : S.open.add(s.id); render(); } },
        `${open ? 'Hide' : 'Show'} tasks (${s.done_task_count}/${s.task_count})`),
      select(STORY_STATUSES, s.status, act((v) => api(`/stories/${s.id}`, { method: 'PATCH', body: { status: v } })), { 'aria-label': 'Story status', disabled: archived }),
      h('button', { class: 'btn small', disabled: archived, onclick: () => storyForm(s) }, 'Edit'),
      h('button', { class: 'btn small danger', disabled: archived, onclick: act(async () => {
        if (confirm(`Delete story "${s.title}" and its ${s.task_count} task(s)?`)) await api(`/stories/${s.id}`, { method: 'DELETE' });
      }) }, 'Delete')),
    open && h('div', { class: 'tasks' },
      s.tasks.map((t) => taskRow(t, archived)),
      h('div', {}, h('button', { class: 'btn small', disabled: archived, onclick: () => taskForm(s) }, 'Add task'))),
  );
}

function taskRow(t, archived) {
  const overdue = t.due_date && t.status !== 'done' && t.due_date < today();
  const who = userName(t.assignee_id);
  return h('div', { class: 'task ' + t.status },
    h('span', { class: 'ttitle' }, t.title),
    h('span', { class: 'row' },
      select(TASK_STATUSES, t.status, act((v) => api(`/tasks/${t.id}`, { method: 'PATCH', body: { status: v } })), { 'aria-label': 'Task status', disabled: archived }),
      h('button', { class: 'btn small', disabled: archived, onclick: () => taskForm(null, t) }, 'Edit'),
      h('button', { class: 'btn small danger', disabled: archived, 'aria-label': 'Delete task', onclick: act(async () => {
        if (confirm(`Delete task "${t.title}"?`)) await api(`/tasks/${t.id}`, { method: 'DELETE' });
      }) }, 'Delete')),
    h('div', { class: 'tmeta' }, h('span', {}, who || 'Unassigned'),
      t.due_date && h('span', { class: overdue ? 'overdue' : '' }, (overdue ? 'Overdue: ' : 'Due ') + t.due_date),
      t.description && h('span', {}, t.description)),
  );
}

/* ---------------------------------------------------------------- forms (dialog) */
function openForm({ title, fields, submit = 'Save', onSubmit }) {
  const dlg = $('#dlg');
  dlg.className = '';
  const err = h('p', { class: 'err', role: 'alert' });
  const form = h('form', {
    method: 'dialog',
    onsubmit: async (e) => {
      e.preventDefault();
      const values = {};
      for (const f of fields) {
        const v = form.elements[f.name].value.trim();
        values[f.name] = f.type === 'number' || f.cast === 'int' ? (v === '' ? null : Number(v)) : f.type === 'date' ? v || null : v;
      }
      try { await onSubmit(values); dlg.close(); await reload(); } catch (ex) { err.textContent = ex.message; }
    },
  },
    h('h2', {}, title),
    fields.map((f) => h('label', {}, f.label,
      f.type === 'textarea' ? h('textarea', { name: f.name, maxlength: f.max || 5000 }, f.value || '')
        : f.type === 'select' ? select(f.options, f.value, () => {}, { name: f.name })
        : h('input', { name: f.name, type: f.type || 'text', value: f.value ?? '', required: f.required, maxlength: f.max, min: f.min, max: f.maxv }))),
    err,
    h('div', { class: 'row end' }, h('button', { type: 'button', class: 'btn', onclick: () => dlg.close() }, 'Cancel'), h('button', { type: 'submit', class: 'btn primary' }, submit)),
  );
  dlg.replaceChildren(form);
  dlg.showModal();
  const first = form.querySelector('input, textarea'); if (first) first.focus();
}

const people = () => [['', 'Unassigned'], ...S.users.map((u) => [String(u.id), u.name])];

function newProjectForm() {
  openForm({ title: 'New project', submit: 'Create', fields: [
    { name: 'name', label: 'Name', required: true, max: 120 }, { name: 'description', label: 'Description', type: 'textarea', max: 2000 }],
  onSubmit: async (v) => { const p = await api('/projects', { method: 'POST', body: v }); S.pid = p.id; } });
}

function editProjectForm() {
  const p = S.project;
  openForm({ title: 'Edit project', fields: [
    { name: 'name', label: 'Name', required: true, max: 120, value: p.name }, { name: 'description', label: 'Description', type: 'textarea', max: 2000, value: p.description }],
  onSubmit: (v) => api(`/projects/${p.id}`, { method: 'PATCH', body: v }) });
}

function storyForm(s) {
  openForm({ title: s ? 'Edit story' : 'New user story', submit: s ? 'Save' : 'Add story', fields: [
    { name: 'title', label: 'Title (As a user, I want...)', required: true, max: 200, value: s?.title },
    { name: 'description', label: 'Description / acceptance criteria', type: 'textarea', value: s?.description },
    { name: 'priority', label: 'Priority', type: 'select', options: PRIORITIES, value: s?.priority || 'medium' },
    { name: 'points', label: 'Story points (0-100)', type: 'number', min: 0, maxv: 100, value: s?.points },
    { name: 'assignee_id', label: 'Assignee', type: 'select', cast: 'int', options: people(), value: s?.assignee_id },
  ],
  onSubmit: (v) => s ? api(`/stories/${s.id}`, { method: 'PATCH', body: v }) : api(`/projects/${S.pid}/stories`, { method: 'POST', body: v }) });
}

function taskForm(story, t) {
  openForm({ title: t ? 'Edit task' : 'New task', submit: t ? 'Save' : 'Add task', fields: [
    { name: 'title', label: 'Title', required: true, max: 200, value: t?.title },
    { name: 'description', label: 'Details', type: 'textarea', value: t?.description },
    { name: 'assignee_id', label: 'Assignee (they get a notification)', type: 'select', cast: 'int', options: people(), value: t?.assignee_id },
    { name: 'due_date', label: 'Due date', type: 'date', value: t?.due_date },
  ],
  onSubmit: async (v) => {
    if (t) await api(`/tasks/${t.id}`, { method: 'PATCH', body: v });
    else { await api(`/stories/${story.id}/tasks`, { method: 'POST', body: v }); S.open.add(story.id); }
  } });
}

/* ---------------------------------------------------------------- async report (polls the job) */
async function requestReport() {
  try {
    const job = await api(`/projects/${S.pid}/reports`, { method: 'POST' });
    showReport(job.id);
  } catch (e) { toast(e.message, true); }
}

async function showReport(jobId) {
  const dlg = $('#dlg');
  dlg.className = 'wide';
  const body = h('div', { class: 'dbody' }, h('h2', {}, 'Project report'), h('p', { class: 'status-line' }, 'Queued...'));
  dlg.replaceChildren(body);
  if (!dlg.open) dlg.showModal();
  let closed = false;
  dlg.addEventListener('close', () => { closed = true; }, { once: true });
  let history = [];
  try { history = (await api(`/projects/${S.pid}/reports`)).filter((j) => j.status === 'succeeded' && j.id !== jobId); } catch { /* optional */ }
  while (!closed) {
    let job;
    try { job = await api(`/jobs/${jobId}`); } catch (e) { body.replaceChildren(h('p', { class: 'err' }, e.message)); return; }
    paintReport(body, job, history);
    if (job.status === 'succeeded' || job.status === 'dead') return;
    await new Promise((r) => setTimeout(r, 1000));
  }
}

function paintReport(body, job, history) {
  const msg = {
    pending: job.attempts ? `Attempt ${job.attempts} failed (${job.last_error}). Retrying with backoff...` : 'Queued - waiting for the background worker...',
    running: 'Worker is generating the report...',
    succeeded: `Done - succeeded on attempt ${job.attempts} of ${job.max_attempts}.`,
    dead: `Failed permanently after ${job.attempts} attempts: ${job.last_error}`,
  }[job.status];
  body.replaceChildren();
  append(body, [
    h('h2', {}, 'Project report'),
    h('p', { class: 'status-line ' + job.status }, `Job #${job.id}: ${msg}`),
    job.result && reportView(job.result),
    history.length > 0 && h('div', { class: 'row' }, h('span', {}, 'Earlier reports:'),
      history.slice(0, 5).map((j) => h('button', { class: 'btn small', onclick: () => showReport(j.id) }, `#${j.id} ${when(j.created_at)}`))),
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: () => $('#dlg').close() }, 'Close')),
  ]);
}

function reportView(r) {
  const kv = (label, value) => h('div', {}, h('b', {}, value), h('small', {}, label));
  const tbl = (head, rows) => h('table', {}, h('thead', {}, h('tr', {}, head.map((x) => h('th', {}, x)))),
    h('tbody', {}, rows.map((row) => h('tr', {}, row.map((c) => h('td', {}, c))))));
  const label = (k) => k.replace('_', ' ');
  return h('div', { class: 'dbody', style: 'padding:0' },
    h('div', { class: 'kv' }, kv('complete', r.completion_percent + '%'), kv('stories', r.stories.total), kv('tasks', r.tasks.total),
      kv('story points done', `${r.story_points.done}/${r.story_points.total}`), kv('unassigned tasks', r.tasks.unassigned)),
    tbl(['Stories', 'Count'], Object.entries(r.stories.by_status).map(([k, v]) => [label(k), v])),
    tbl(['Tasks', 'Count'], Object.entries(r.tasks.by_status).map(([k, v]) => [label(k), v])),
    r.workload.length > 0 && tbl(['Workload', 'Open', 'Done'], r.workload.map((w) => [w.user, w.open_tasks, w.done_tasks])),
    r.overdue_tasks.length > 0 ? tbl(['Overdue task', 'Due', 'Assignee'], r.overdue_tasks.map((t) => [t.title, t.due_date, t.assignee])) : h('p', {}, 'No overdue tasks.'),
    h('small', {}, 'Generated ' + r.generated_at.replace('T', ' ').replace('Z', ' UTC')),
  );
}

/* ---------------------------------------------------------------- boot */
(async function boot() {
  if (S.token) {
    try { S.me = await api('/auth/me'); await loadAll(); }
    catch { S.token = null; S.me = null; localStorage.removeItem('sb_token'); }
  }
  render();
  setInterval(() => { if (S.me) loadNotifs(); }, 8000);
})();

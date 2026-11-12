/*
 * AgroStar — Farm Reminders engine.
 *
 * Farmers set a reminder ("Apply fertilizer for Rice, remind me in 5
 * days") from the dedicated Reminders page or a quick "Set Reminder"
 * button on the Fertilizer Plan / Growth Monitoring / Pest Detection
 * pages. Reminders are stored in localStorage (works fully offline,
 * matches how theme/language already persist in this app — no backend
 * needed) and are checked:
 *   1. Every time any page loads (DOMContentLoaded below), so a
 *      reminder that came due while the app was closed is caught the
 *      moment the farmer reopens it — the realistic behaviour for a
 *      browser-based PWA with no push server.
 *   2. Every 30 seconds while a page stays open, so a live alarm fires
 *      (sound + vibration + a real browser Notification, or an in-app
 *      toast if notification permission hasn't been granted) right at
 *      the due time if the farmer happens to be in the app.
 *
 * Loaded on every page via base.html, right after i18n.js/app.js, so
 * t() and the shared translations are already available.
 */

const REMINDERS_KEY = 'agrostar_reminders';
const DAY_MS = 24 * 60 * 60 * 1000;

function getReminders(){
  try {
    const raw = localStorage.getItem(REMINDERS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (e) {
    return [];
  }
}

function saveReminders(list){
  try { localStorage.setItem(REMINDERS_KEY, JSON.stringify(list)); }
  catch (e) { /* storage full/unavailable — fail silently, demo-safe */ }
}

function addReminder({ crop, task, days }){
  const list = getReminders();
  const n = Math.max(1, parseInt(days, 10) || 1);
  const reminder = {
    id: 'r' + Date.now() + Math.floor(Math.random() * 1000),
    crop: crop || 'Field',
    task: task || 'Check crop',
    days: n,
    createdAt: Date.now(),
    dueAt: Date.now() + n * DAY_MS,
    done: false,
    notified: false,
  };
  list.push(reminder);
  saveReminders(list);
  updateReminderBadges();
  return reminder;
}

function completeReminder(id){
  saveReminders(getReminders().map(r => (r.id === id ? { ...r, done: true } : r)));
  updateReminderBadges();
}

function reopenReminder(id){
  saveReminders(getReminders().map(r => (r.id === id ? { ...r, done: false, notified: false } : r)));
  updateReminderBadges();
}

function deleteReminder(id){
  saveReminders(getReminders().filter(r => r.id !== id));
  updateReminderBadges();
}

function activeReminders(){
  return getReminders().filter(r => !r.done).sort((a, b) => a.dueAt - b.dueAt);
}

function dueReminders(){
  const now = Date.now();
  return activeReminders().filter(r => r.dueAt <= now);
}

function completedReminders(){
  return getReminders().filter(r => r.done).sort((a, b) => b.dueAt - a.dueAt);
}

// Human-friendly "in 3 days" / "Due today" / "Overdue by 2 days" label,
// built from a handful of translated words rather than a full i18n
// template string, so it reads naturally in English/Hindi/Odia.
function reminderDueLabel(dueAt){
  const now = Date.now();
  const diffDays = Math.round((dueAt - now) / DAY_MS);
  if (diffDays === 0) return t('txt_due_today');
  if (diffDays === 1) return t('txt_due_tomorrow');
  if (diffDays > 1) return `${t('txt_in')} ${diffDays} ${t('unit_days')}`;
  const overdue = Math.abs(diffDays);
  const unit = overdue === 1 ? t('unit_day') : t('unit_days');
  return `${t('txt_overdue_by')} ${overdue} ${unit}`;
}

// ---------------------------------------------------------------------
// Alarm — sound (Web Audio, no asset file needed) + vibration + a real
// browser Notification when permission is granted, else a visible
// in-app toast so the alert is never silently missed.
// ---------------------------------------------------------------------
function playAlarmBeep(){
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const gain = ctx.createGain();
    gain.connect(ctx.destination);
    const osc = ctx.createOscillator();
    osc.type = 'sine';
    osc.frequency.value = 880;
    osc.connect(gain);
    const t0 = ctx.currentTime;
    [0, 0.35, 0.7].forEach(offset => {
      gain.gain.setValueAtTime(0.0001, t0 + offset);
      gain.gain.exponentialRampToValueAtTime(0.22, t0 + offset + 0.04);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + offset + 0.28);
    });
    osc.start(t0);
    osc.stop(t0 + 1.1);
    osc.onended = () => ctx.close();
  } catch (e) { /* audio not available — alarm still shows visually */ }

  if (navigator.vibrate) {
    try { navigator.vibrate([200, 100, 200, 100, 200]); } catch (e) {}
  }
}

function showAlarmToast(title, body){
  let toast = document.getElementById('agrostarAlarmToast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'agrostarAlarmToast';
    toast.className = 'alarm-toast';
    toast.onclick = () => { location.href = '/feature/reminders'; };
    document.body.appendChild(toast);
  }
  toast.innerHTML = `<div class="alarm-toast-title">⏰ ${title}</div><div class="alarm-toast-body">${body}</div>`;
  requestAnimationFrame(() => toast.classList.add('show'));
  clearTimeout(toast._hideTimer);
  toast._hideTimer = setTimeout(() => toast.classList.remove('show'), 7000);
}

function fireReminderAlarm(reminder){
  playAlarmBeep();
  const title = 'AgroStar ⏰';
  const body = `${reminder.task} — ${reminder.crop}`;
  if ('Notification' in window && Notification.permission === 'granted') {
    try {
      new Notification(title, { body, icon: '/static/icons/icon-192.png', tag: reminder.id });
    } catch (e) {
      showAlarmToast(title, body);
    }
  } else {
    showAlarmToast(title, body);
  }
}

function requestReminderNotificationPermission(){
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission().then(() => {
      document.dispatchEvent(new CustomEvent('agrostar:notifpermchange'));
    });
  }
}

// Checks every active reminder against "now"; anything newly due gets an
// alarm fired exactly once (notified:true stops repeat alarms), but stays
// visible in the due/overdue list until the farmer marks it done.
function checkDueReminders(){
  const now = Date.now();
  const list = getReminders();
  let changed = false;
  list.forEach(r => {
    if (!r.done && !r.notified && r.dueAt <= now) {
      fireReminderAlarm(r);
      r.notified = true;
      changed = true;
    }
  });
  if (changed) saveReminders(list);
  updateReminderBadges();
}

// Keeps any .reminder-badge-count element (dashboard bell, nav, etc.) in
// sync with how many reminders are currently due/overdue.
function updateReminderBadges(){
  const count = dueReminders().length;
  document.querySelectorAll('.reminder-badge-count').forEach(el => {
    if (count > 0) {
      el.textContent = count > 9 ? '9+' : String(count);
      el.style.display = 'flex';
    } else {
      el.style.display = 'none';
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  checkDueReminders();
  setInterval(checkDueReminders, 30000);
});

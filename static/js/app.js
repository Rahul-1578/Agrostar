// AgroStar — shared client-side helpers.
// Page-specific logic lives in each template's extra_scripts block;
// this file is for cross-page utilities (kept light for demo).

console.log("AgroStar loaded — Sow Smart, Reap Big 🌾");

// ---------------------------------------------------------------------
// Dark mode — persisted in localStorage so it stays on across every
// page and every visit. The actual <html data-theme="dark"> attribute
// is set by a tiny inline script in base.html's <head>, BEFORE this
// file (or any CSS-dependent content) loads, so there's no flash of
// the light theme on page load. This file just keeps the toggle
// switch (Profile ▸ Display) and the theme-color meta tag in sync.
// ---------------------------------------------------------------------
function setTheme(theme){
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('agrostar_theme', theme);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', theme === 'dark' ? '#12180F' : '#2D5F3E');
  document.querySelectorAll('.theme-toggle-input').forEach(el => { el.checked = (theme === 'dark'); });
}
function toggleTheme(checked){
  setTheme(checked ? 'dark' : 'light');
}
// Sync any toggle switch present on this page (e.g. Profile) with
// whatever theme is currently active.
document.addEventListener('DOMContentLoaded', () => {
  const current = document.documentElement.getAttribute('data-theme') || 'light';
  document.querySelectorAll('.theme-toggle-input').forEach(el => { el.checked = (current === 'dark'); });
});


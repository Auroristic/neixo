async function pollLogs() {
  const el = document.getElementById("logs");
  if (!el) return;
  try {
    const r = await fetch("/admin/logs/recent");
    if (!r.ok) return;
    const j = await r.json();
    el.textContent = j.lines.join("\n");
    el.scrollTop = el.scrollHeight;
  } catch {
    /* server restarting or offline; retry next tick */
  }
}
setInterval(pollLogs, 2000);
pollLogs();

document.querySelectorAll("form[data-confirm]").forEach((f) => {
  f.addEventListener("submit", (e) => {
    if (!confirm(f.dataset.confirm)) e.preventDefault();
  });
});

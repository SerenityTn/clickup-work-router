(() => {
  const root = document.createElement('div');
  root.className = 'clickup-work-router';
  root.innerHTML = `
    <h2>ClickUp Router</h2>
    <p>ClickUp webhook routing into Hermes Kanban.</p>
    <section><h3>Health</h3><pre id="cwr-health">Loading...</pre></section>
    <section><h3>Workspaces</h3><pre id="cwr-workspaces"></pre></section>
    <section><h3>Recent Events</h3><pre id="cwr-events"></pre></section>
  `;
  const mount = document.getElementById('plugin-root') || document.body;
  mount.appendChild(root);
  async function load() {
    try {
      const res = await fetch('/api/plugins/clickup-work-router/status');
      const data = await res.json();
      document.getElementById('cwr-health').textContent = JSON.stringify(data.service || data, null, 2);
      document.getElementById('cwr-workspaces').textContent = JSON.stringify(data.workspaces || {}, null, 2);
      document.getElementById('cwr-events').textContent = JSON.stringify(data.recent_events || [], null, 2);
    } catch (err) {
      document.getElementById('cwr-health').textContent = 'Status API unavailable: ' + err;
    }
  }
  load();
})();

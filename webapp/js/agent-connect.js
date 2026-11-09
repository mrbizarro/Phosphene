(async function () {
  const select = document.getElementById('client');
  const command = document.getElementById('command');
  const button = document.getElementById('copy');
  const status = document.getElementById('status');
  try {
    const response = await fetch('/agents/info');
    if (!response.ok) throw new Error('Phosphene is not responding. Reopen this page from Help.');
    const info = await response.json();
    const show = () => {
      const value = info.agents[select.value];
      command.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
      button.textContent = select.value === 'mcp_config' ? 'Copy configuration' : 'Copy command';
      status.textContent = '';
    };
    select.addEventListener('change', show);
    show();
    button.disabled = false;
    button.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(command.textContent); status.textContent = 'Copied.'; }
      catch (_) { status.textContent = 'Select the text above and copy it.'; }
    });
  } catch (error) { command.textContent = ''; status.textContent = error.message; }
})();

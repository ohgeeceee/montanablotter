/* User-triggered clipboard and print controls; no tracking or outbound requests. */
document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-copy-target], [data-print]');
  if (!button) return;
  if (button.hasAttribute('data-print')) {
    window.print();
    return;
  }
  const target = document.getElementById(button.dataset.copyTarget);
  const status = button.closest('.ad-card, .ad-share-tools')?.querySelector('[data-copy-status]');
  if (!target) return;
  try {
    await navigator.clipboard.writeText(target.value || target.textContent);
    if (status) status.textContent = 'Copied. Ready to paste.';
  } catch (_) {
    target.focus();
    if (target.select) target.select();
    if (status) status.textContent = 'Select the text and use your browser’s Copy command.';
  }
});

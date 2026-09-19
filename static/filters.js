// Static directory filters: all matching happens locally, without Render/API calls.
(() => {
  const form = document.querySelector('[data-static-filter]');
  if (!form) return;
  const normalize = value => (value || '').normalize('NFKC').toLocaleLowerCase('ja').trim();
  const rows = [...document.querySelectorAll('[data-filter-row]')].map(element => ({
    element,
    league: element.dataset.league,
    clubs: (element.dataset.clubs || '').split(/\s+/),
    search: normalize(element.dataset.search),
  }));
  const status = document.createElement('p');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  form.after(status);
  const fields = ['q', 'league', 'club'];
  function apply() {
    const values = new FormData(form);
    const query = normalize(values.get('q'));
    const league = values.get('league');
    const club = values.get('club');
    let count = 0;
    rows.forEach(row => {
      const show = (!query || row.search.includes(query)) &&
        (!league || row.league === league) && (!club || row.clubs.includes(club));
      row.element.style.display = show ? '' : 'none';
      if (show) count++;
    });
    status.textContent = count ? `${count.toLocaleString('ja-JP')}件を表示` : '条件に一致する情報がありません。';
  }
  function restore() {
    const params = new URLSearchParams(location.search);
    fields.forEach(name => {
      const input = form.elements.namedItem(name);
      if (input) input.value = params.get(name) || '';
    });
    apply();
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    const url = new URL(location.href);
    const values = new FormData(form);
    fields.forEach(name => {
      const value = String(values.get(name) || '').trim();
      if (value) url.searchParams.set(name, value);
      else url.searchParams.delete(name);
    });
    history.pushState(null, '', url);
    apply();
  });
  window.addEventListener('popstate', restore);
  restore();
})();

(() => {
  const root = document.querySelector('.sapporo-hub');
  if (!root) return;
  root.querySelectorAll('[data-hub-tabs]').forEach(group => {
    const tabs = [...group.querySelectorAll('[data-hub-tab]')];
    const panels = [...group.querySelectorAll('[data-hub-panel]')];
    group.querySelector('nav').setAttribute('role', 'tablist');
    function activate(tab, focus = false) {
      tabs.forEach(t => {
        const active = t === tab;
        t.setAttribute('aria-selected', String(active));
        t.tabIndex = active ? 0 : -1;
      });
      panels.forEach(p => { p.hidden = p.id !== tab.dataset.hubTab; });
      if (focus) tab.focus();
    }
    tabs.forEach((tab, index) => {
      tab.id = 'tab-' + tab.dataset.hubTab;
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-controls', tab.dataset.hubTab);
      const panel = panels.find(p => p.id === tab.dataset.hubTab);
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', tab.id);
      panel.tabIndex = 0;
      tab.addEventListener('click', event => { event.preventDefault(); activate(tab); });
      tab.addEventListener('keydown', event => {
        let next;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = tabs.length - 1;
        if (next !== undefined) { event.preventDefault(); activate(tabs[next], true); }
      });
    });
    activate(tabs.find(t => '#' + t.dataset.hubTab === location.hash) || tabs[0]);
  });
  root.querySelectorAll('[data-hub-filter]').forEach(group => {
    const rows = [...group.querySelectorAll('[data-filter-item]')];
    const buttons = [...group.querySelectorAll('[data-filter]')];
    const more = group.querySelector('[data-filter-more]');
    const search = group.querySelector('[data-player-search]');
    const count = group.querySelector('[data-filter-count]');
    const isPlayers = group.dataset.hubFilter === 'players';
    const limit = isPlayers ? 12 : 6;
    let category = 'all', expanded = false;
    const normalize = value => value.normalize('NFKC').toLowerCase().replace(/\s/g, '');
    function update() {
      const query = normalize(search?.value || '');
      const matching = rows.filter(row => (category === 'all' || row.dataset.category === category) && normalize(row.dataset.search || '').includes(query));
      rows.forEach(row => { row.hidden = true; });
      (expanded ? matching : matching.slice(0, limit)).forEach(row => { row.hidden = false; });
      count.textContent = matching.length ? `${matching.length}${isPlayers ? '選手' : '試合'}${!expanded && matching.length > limit ? ` / ${limit}${isPlayers ? '選手' : '試合'}を表示` : ''}` : '該当する選手はいません';
      more.hidden = matching.length <= limit;
      more.textContent = expanded ? (isPlayers ? '12選手に戻す' : '6試合に戻す') : `残り${matching.length - limit}${isPlayers ? '選手' : '試合'}を表示`;
      buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.filter === category)));
    }
    buttons.forEach(button => button.addEventListener('click', () => { category = button.dataset.filter; expanded = false; update(); }));
    search?.addEventListener('input', () => { expanded = false; update(); });
    more.addEventListener('click', () => { expanded = !expanded; update(); });
    group.querySelector('[data-js-control]').hidden = false;
    update();
  });
})();

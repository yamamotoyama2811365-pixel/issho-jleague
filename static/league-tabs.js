// League navigation and directory filters work entirely in the browser.
(() => {
  const normalize = value => (value || '').normalize('NFKC').toLocaleLowerCase('ja').replace(/\s+/g, '').trim();
  document.querySelectorAll('[data-tabs]').forEach((group, groupIndex) => {
    const list = group.querySelector('[data-tab-list]');
    const tabs = [...list.querySelectorAll('[data-tab]')];
    const panels = [...group.querySelectorAll('[data-tab-panel]')];
    let active = tabs[0].dataset.tab;
    list.setAttribute('role', 'tablist');
    tabs.forEach((tab, index) => {
      tab.id = `league-tab-${groupIndex}-${index}`;
      tab.setAttribute('role', 'tab');
      const panel = panels.find(p => p.dataset.tabPanel === tab.dataset.tab);
      tab.setAttribute('aria-controls', panel.id);
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', tab.id);
    });
    function filterClubs() {
      const input = group.querySelector('[data-club-search]');
      if (!input) return;
      const query = normalize(input.value);
      let count = 0;
      panels.forEach(panel => {
        panel.querySelectorAll('[data-club-card]').forEach(card => {
          card.hidden = !normalize(card.dataset.search).includes(query);
          if (panel.dataset.tabPanel === active && !card.hidden) count++;
        });
      });
      group.querySelector('[data-club-count]').textContent = count ? `${active.toUpperCase()} · ${count}クラブ（順位順）` : '該当するクラブがありません。クラブ名・会場名やリーグを変えて検索してください。';
    }
    function activate(key, updateURL = false) {
      if (!tabs.some(t => t.dataset.tab === key)) return;
      active = key;
      tabs.forEach(tab => {
        const selected = tab.dataset.tab === key;
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
      });
      panels.forEach(panel => { panel.hidden = panel.dataset.tabPanel !== key; });
      filterClubs();
      if (updateURL) {
        const url = new URL(location.href);
        url.hash = panels.find(p => p.dataset.tabPanel === key).id;
        if (group.hasAttribute('data-query-league')) {
          url.searchParams.delete('league');
          url.searchParams.delete('club');
          panels.forEach(panel => {
            const select = panel.querySelector('[data-fixture-club]');
            if (select) { select.value = ''; filterMatches(panel); }
          });
        }
        history.pushState(null, '', url);
      }
    }
    function fromURL() {
      const hashPanel = panels.find(p => `#${p.id}` === location.hash);
      const params = new URLSearchParams(location.search);
      let key = hashPanel?.dataset.tabPanel;
      if (!key && group.hasAttribute('data-query-league')) {
        key = (params.get('league') || '').toLowerCase();
        const club = params.get('club');
        if (!key && club) {
          key = panels.find(p => [...p.querySelectorAll('[data-fixture-club] option')].some(o => o.value === club))?.dataset.tabPanel;
        }
      }
      activate(key || tabs[0].dataset.tab);
      panels.forEach(panel => {
        const select = panel.querySelector('[data-fixture-club]');
        if (select) {
          const club = params.get('club') || '';
          select.value = [...select.options].some(o => o.value === club) ? club : '';
          filterMatches(panel);
        }
      });
    }
    function filterMatches(panel) {
      const select = panel.querySelector('[data-fixture-club]');
      if (!select) return;
      let count = 0;
      panel.querySelectorAll('[data-match-key]').forEach(card => {
        card.hidden = !!select.value && !card.dataset.clubs.split(/\s+/).includes(select.value);
        if (!card.hidden) count++;
      });
      panel.querySelector('[data-match-count]').textContent = count ? `${count}試合を表示` : 'このクラブの今後の日程は現在ありません。';
    }
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', event => { event.preventDefault(); activate(tab.dataset.tab, true); });
      tab.addEventListener('keydown', event => {
        let next;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = tabs.length - 1;
        if (next !== undefined) { event.preventDefault(); activate(tabs[next].dataset.tab, true); tabs[next].focus(); }
      });
    });
    group.querySelector('[data-club-search]')?.addEventListener('input', filterClubs);
    panels.forEach(panel => {
      panel.querySelector('[data-fixture-club]')?.addEventListener('change', event => {
        filterMatches(panel);
        const url = new URL(location.href);
        url.hash = panel.id;
        url.searchParams.delete('league');
        if (event.target.value) url.searchParams.set('club', event.target.value);
        else url.searchParams.delete('club');
        history.replaceState(null, '', url);
      });
    });
    window.addEventListener('hashchange', fromURL);
    window.addEventListener('popstate', fromURL);
    fromURL();
  });
})();

(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', () => {
    if (!window.React || !window.ReactDOM || !window.NVBConsole) return;
    const { createElement, useEffect, useState } = window.React;

    function useDashboardSnapshot() {
      const [snapshot, setSnapshot] = useState(null);
      useEffect(() => window.NVBConsole.subscribe(setSnapshot), []);
      return snapshot;
    }

    function Hero() {
      return createElement(React.Fragment, null,
        createElement('div', null,
          createElement('span', { className: 'eyebrow' }, 'AI COMMAND CENTER'),
          createElement('h1', null, 'Policy overview'),
          createElement('p', null, 'Understand demand. Prioritize investment. Track public outcomes.')
        ),
        createElement('a', { className: 'btn btn-primary', href: '#policyWorkbench' },
          createElement('i', { className: 'bi bi-sliders2', 'aria-hidden': true }),
          'Open policy workspace'
        )
      );
    }

    function InsightCard() {
      const snapshot = useDashboardSnapshot();
      if (!snapshot) return null;
      const copy = window.NVBConsole.insightCopy(snapshot);
      return createElement('div', { className: 'insight-card' },
        createElement('i', { className: 'bi bi-stars insight-symbol', 'aria-hidden': true }),
        createElement('div', { className: 'insight-body' },
          createElement('h2', null, copy.title),
          createElement('p', null, copy.body),
          createElement('span', { className: 'insight-source' }, copy.source)
        ),
        snapshot.role === 'analyst' && createElement('a', { href: '#priorityPanel', className: 'btn btn-secondary' }, 'Explore priorities')
      );
    }

    const hero = document.getElementById('commandHero');
    const insight = document.getElementById('policyInsight');
    if (!hero || !insight) return;
    insight.dataset.reactMounted = 'true';
    ReactDOM.createRoot(hero).render(createElement(Hero));
    ReactDOM.createRoot(insight).render(createElement(InsightCard));
  });
})();

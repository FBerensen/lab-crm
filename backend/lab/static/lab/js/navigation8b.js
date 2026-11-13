(() => {
    'use strict';
    const init = () => {
        const data = document.getElementById('lab8b-navigation-data');
        if (!data) return;
        const sidebar = document.getElementById('nav-sidebar');
        if (sidebar && data.dataset.modelUrl) {
            for (const link of sidebar.querySelectorAll('a[href]')) {
                if (link.getAttribute('href') === data.dataset.modelUrl) {
                    const row = link.closest('tr');
                    if (row) row.classList.add('current-model');
                    link.setAttribute('aria-current', 'page');
                }
            }
        }
        if (data.dataset.modelUrl && data.dataset.sectionUrl) {
            for (const link of document.querySelectorAll('.breadcrumbs a[href]')) {
                if (link.getAttribute('href') === data.dataset.labRoot) {
                    link.setAttribute('href', data.dataset.sectionUrl);
                    link.textContent = data.dataset.sectionTitle;
                }
            }
        }
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();

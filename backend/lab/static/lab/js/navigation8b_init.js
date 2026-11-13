(() => {
    'use strict';
    try {
        if (!localStorage.getItem('labcrm.nav8b.initialized')) {
            localStorage.setItem('django.admin.navSidebarIsOpen', 'true');
            localStorage.setItem('labcrm.nav8b.initialized', '1');
        }
        const group = document.currentScript ? document.currentScript.dataset.section : '';
        if (sessionStorage.getItem('labcrm.nav8b.section') !== group) {
            sessionStorage.removeItem('django.admin.navSidebarFilterValue');
            localStorage.removeItem('django.admin.navSidebarFilterValue');
            sessionStorage.setItem('labcrm.nav8b.section', group);
        }
    } catch (error) {
        // Browser storage restrictions must not break the page.
    }
})();

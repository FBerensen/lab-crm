/* Vue.js 3 приложение визуализации Lab CRM (папка frontend/static/js/) */
(function () {
  if (!window.Vue) {
    const el = document.getElementById("lab-dashboard-app");
    if (el) {
      el.innerHTML =
        '<div style="padding:24px;">Не удалось загрузить библиотеку Vue.js. Проверьте подключение к сети или используйте панель <a href="/admin/">/admin/</a>.</div>';
    }
    return;
  }

  const { createApp } = window.Vue;

  const app = createApp({
    delimiters: ["[[", "]]"],
    data() {
      return {
        loading: true,
        error: null,
        activeTab: "stock",
        stockSearch: "",
        onlyAlerts: false,
        summary: {
          projects_count: 0,
          samples_count: 0,
          active_tasks_count: 0,
          overdue_tasks_count: 0,
          lots_count: 0,
          alerts_count: 0,
        },
        stockBalance: [],
        storageStats: [],
        samplesByKind: [],
        tasks: [],
      };
    },
    computed: {
      filteredStock() {
        const q = this.stockSearch.trim().toLowerCase();
        return this.stockBalance.filter((item) => {
          if (this.onlyAlerts && item.status === "ok") {
            return false;
          }
          if (!q) {
            return true;
          }
          return (
            item.name.toLowerCase().includes(q) ||
            item.code.toLowerCase().includes(q) ||
            item.category.toLowerCase().includes(q)
          );
        });
      },
    },
    methods: {
      async fetchDashboard() {
        this.loading = true;
        this.error = null;
        try {
          const response = await fetch("/api/v1/dashboard/", {
            headers: { Accept: "application/json" },
            credentials: "same-origin",
          });
          if (!response.ok) {
            throw new Error("Ошибка загрузки данных API: HTTP " + response.status);
          }
          const payload = await response.json();
          this.summary = payload.summary || this.summary;
          this.stockBalance = payload.stock_balance || [];
          this.storageStats = payload.storage_stats || [];
          this.samplesByKind = payload.samples_by_kind || [];
          this.tasks = payload.tasks || [];
        } catch (err) {
          this.error = err.message || "Не удалось получить данные с сервера.";
        } finally {
          this.loading = false;
        }
      },
    },
    mounted() {
      this.fetchDashboard();
    },
  });

  app.mount("#lab-dashboard-app");
})();

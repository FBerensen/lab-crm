<template>
  <div style="display: flex; min-height: 100vh;">
    <aside style="width: 280px; background: #0f172a; color: #f8fafc; padding: 20px; display: flex; flex-direction: column; gap: 8px;">
      <div style="padding-bottom: 14px; border-bottom: 1px solid #1e293b; margin-bottom: 8px;">
        <h2 style="margin: 0; font-size: 20px;">🧬 LabCRM</h2>
        <div style="margin-top: 6px; display: flex; gap: 6px; flex-wrap: wrap;">
          <span style="background: #065f46; color: #a7f3d0; font-size: 11px; padding: 2px 8px; border-radius: 99px;">Django 5.2 Backend</span>
          <span style="background: #1e40af; color: #bfdbfe; font-size: 11px; padding: 2px 8px; border-radius: 99px;">Vue 3 Frontend</span>
        </div>
      </div>

      <router-link
        v-for="item in navItems"
        :key="item.to"
        :to="item.to"
        style="color: #cbd5e1; text-decoration: none; padding: 10px 12px; border-radius: 8px; display: block;"
        active-class="active-nav"
      >
        <div style="font-weight: 600; font-size: 14px;">{{ item.title }}</div>
        <div style="color: #64748b; font-size: 11px; margin-top: 2px;">{{ item.folder }}</div>
      </router-link>

      <div style="margin-top: auto; padding-top: 16px; border-top: 1px solid #1e293b; font-size: 13px;">
        <a href="http://127.0.0.1:8000/admin/" target="_blank" style="color: #38bdf8; text-decoration: none; display: block; margin-bottom: 6px;">
          ⚙️ Открыть Django Admin ↗
        </a>
        <a href="http://127.0.0.1:8000/api/overview/" target="_blank" style="color: #94a3b8; text-decoration: none; display: block;">
          🔌 JSON API Overview ↗
        </a>
      </div>
    </aside>

    <main style="flex: 1; padding: 28px; overflow-x: auto;">
      <router-view />
    </main>
  </div>
</template>

<script setup>
const navItems = [
  { to: '/warehouse', title: '📦 Склад и хранение', folder: 'modules/warehouse/' },
  { to: '/solutions', title: '🧪 Приготовление растворов', folder: 'modules/solutions/' },
  { to: '/dna-extraction', title: '🧬 Выделение ДНК и ПЦР', folder: 'modules/dna_extraction/' },
  { to: '/electrophoresis', title: '⚡ Форезы (Электрофорез)', folder: 'modules/electrophoresis/' },
  { to: '/samples', title: '🌱 Образцы и блоки A1–H12', folder: 'modules/samples/' },
  { to: '/worklog', title: '📋 Задачи и журнал работ', folder: 'modules/worklog/' }
]
</script>

<style>
.active-nav {
  background: #1e293b !important;
  color: #ffffff !important;
  border-left: 4px solid #38bdf8;
}
.card {
  background: #ffffff;
  border-radius: 10px;
  padding: 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  margin-bottom: 20px;
}
table {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
  border-radius: 8px;
  overflow: hidden;
}
th, td {
  padding: 10px 14px;
  border-bottom: 1px solid #e2e8f0;
  text-align: left;
  font-size: 14px;
}
th {
  background: #f8fafc;
  color: #475569;
  font-weight: 600;
}
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 99px;
  font-size: 12px;
  font-weight: 600;
}
</style>

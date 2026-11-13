<template>
  <div>
    <h1>🧪 Приготовление растворов, буферов и автоклавирование</h1>
    <p style="color: #64748b;">Папка разработчика: <code>frontend/src/modules/solutions/</code> ↔ <code>lab/domains/solutions/</code></p>

    <div class="card">
      <h3>Протоколы приготовления (СОП) ({{ protocols.length }})</h3>
      <table>
        <thead>
          <tr>
            <th>Продукт</th>
            <th>Протокол / Версия</th>
            <th>Базовый объём, мл</th>
            <th>pH</th>
            <th>Требуется автоклавирование</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in protocols" :key="p.id">
            <td><strong>{{ p.product }}</strong></td>
            <td>{{ p.title }} (v{{ p.version }})</td>
            <td>{{ p.base_volume_ml }} мл</td>
            <td>{{ p.target_ph }}</td>
            <td>{{ p.requires_autoclaving ? 'Да' : 'Нет' }}</td>
          </tr>
          <tr v-if="!protocols.length"><td colspan="5">Протоколы пока не созданы</td></tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3>Журнал приготовлений ({{ preparations.length }})</h3>
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Дата</th>
            <th>Протокол</th>
            <th>План / Факт, мл</th>
            <th>Исполнитель</th>
            <th>Проведено по складу</th>
            <th>Автоклав</th>
            <th>Допуск</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="prep in preparations" :key="prep.id">
            <td>#{{ prep.id }}</td>
            <td>{{ prep.actual_day }}</td>
            <td>{{ prep.protocol }}</td>
            <td>{{ prep.planned_volume_ml || '—' }} / {{ prep.actual_volume_ml || '—' }}</td>
            <td>{{ prep.actor }}</td>
            <td>{{ prep.processed ? '✅ Да' : 'Черновик' }}</td>
            <td>{{ prep.autoclaving_done ? '✅ Выполнено' : '—' }}</td>
            <td>{{ prep.approved ? '✅ Допущена' : 'Ожидает' }}</td>
          </tr>
          <tr v-if="!preparations.length"><td colspan="8">Записей приготовлений пока нет</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { fetchApi } from '../../services/api'

const protocols = ref([])
const preparations = ref([])
onMounted(async () => {
  try {
    const data = await fetchApi('/api/solutions/')
    protocols.value = data.protocols || []
    preparations.value = data.preparations || []
  } catch (e) {
    console.warn(e)
  }
})
</script>

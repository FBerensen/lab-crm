<template>
  <div>
    <h1>📦 Склад и хранение реактивов</h1>
    <p style="color: #64748b;">Папка разработчика: <code>frontend/src/modules/warehouse/</code> ↔ <code>lab/domains/warehouse/</code></p>

    <div class="card">
      <h3>Партии на складе ({{ lots.length }})</h3>
      <table>
        <thead>
          <tr>
            <th>Код</th>
            <th>Наименование</th>
            <th>Категория</th>
            <th>Физический остаток</th>
            <th>Резерв</th>
            <th>Свободно</th>
            <th>Место хранения</th>
            <th>Годен до</th>
            <th>Допуск</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="lot in lots" :key="lot.id">
            <td><code>{{ lot.code }}</code></td>
            <td><strong>{{ lot.name }}</strong></td>
            <td>{{ lot.category }}</td>
            <td>{{ lot.quantity }} {{ lot.unit }}</td>
            <td>{{ lot.reserved }} {{ lot.unit }}</td>
            <td><strong>{{ lot.free }} {{ lot.unit }}</strong></td>
            <td>{{ lot.storage_place }}</td>
            <td>{{ lot.expiry }}</td>
            <td>{{ lot.released ? '✅ Допущен' : '⏳ На проверке' }}</td>
          </tr>
          <tr v-if="!lots.length">
            <td colspan="9" style="color: #64748b;">Нет записей или сервер Django не запущен на 127.0.0.1:8000</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { fetchApi } from '../../services/api'

const lots = ref([])
onMounted(async () => {
  try {
    const data = await fetchApi('/api/warehouse/')
    lots.value = data.lots || []
  } catch (e) {
    console.warn(e)
  }
})
</script>

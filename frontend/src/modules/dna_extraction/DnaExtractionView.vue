<template>
  <div>
    <h1>🧬 Выделение ДНК и постановка ПЦР</h1>
    <p style="color: #64748b;">Папка разработчика: <code>frontend/src/modules/dna_extraction/</code> ↔ <code>lab/domains/dna_extraction/</code></p>

    <div class="card">
      <h3>Активные методики выделения ДНК и ПЦР ({{ protocols.length }})</h3>
      <table>
        <thead>
          <tr>
            <th>Код</th>
            <th>Название методики</th>
            <th>Версия</th>
            <th>Вид</th>
            <th>Статус</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in protocols" :key="p.id">
            <td><code>{{ p.code }}</code></td>
            <td><strong>{{ p.title }}</strong></td>
            <td>{{ p.version }}</td>
            <td>{{ p.method_kind }}</td>
            <td>{{ p.active ? 'Активна' : 'Архив' }}</td>
          </tr>
          <tr v-if="!protocols.length"><td colspan="5">Методики пока не добавлены</td></tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3>Выполнения методик ({{ runs.length }})</h3>
      <table>
        <thead>
          <tr>
            <th>№</th>
            <th>Задача</th>
            <th>Методика</th>
            <th>Исполнитель</th>
            <th>Проб в запуске</th>
            <th>Статус</th>
            <th>Начало / Завершение</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in runs" :key="r.id">
            <td>#{{ r.id }}</td>
            <td>{{ r.task }}</td>
            <td>{{ r.protocol }}</td>
            <td>{{ r.executor }}</td>
            <td>{{ r.samples_count }}</td>
            <td><strong>{{ r.status }}</strong></td>
            <td>{{ r.started_on }} / {{ r.finished_on }}</td>
          </tr>
          <tr v-if="!runs.length"><td colspan="7">Выполнений пока нет</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { fetchApi } from '../../services/api'

const protocols = ref([])
const runs = ref([])
onMounted(async () => {
  try {
    const data = await fetchApi('/api/dna-extraction/')
    protocols.value = data.protocols || []
    runs.value = data.runs || []
  } catch (e) {
    console.warn(e)
  }
})
</script>

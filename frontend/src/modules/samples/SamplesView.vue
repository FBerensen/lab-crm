<template>
  <div>
    <h1>🌱 Образцы, Паспорта ВИР и Планшеты 96 лунок (A1–H12)</h1>
    <p style="color: #64748b;">Папка разработчика: <code>frontend/src/modules/samples/</code> ↔ <code>lab/domains/samples/</code></p>

    <div class="card">
      <h3>Исходные блоки 96 лунок ({{ blocks.length }})</h3>
      <table>
        <thead>
          <tr>
            <th>Название планшета</th>
            <th>Дата создания</th>
            <th>Исполнитель</th>
            <th>Место хранения</th>
            <th>ДНК / Вода, мкл</th>
            <th>Заполнено лунок</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in blocks" :key="b.id">
            <td><strong>{{ b.name }}</strong></td>
            <td>{{ b.created_on }}</td>
            <td>{{ b.actor }}</td>
            <td>{{ b.storage_place }}</td>
            <td>{{ b.dna_volume_ul }} / {{ b.water_volume_ul }}</td>
            <td>{{ b.wells_count }} / 96</td>
          </tr>
          <tr v-if="!blocks.length"><td colspan="6">Планшеты 96 лунок пока не созданы</td></tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3>Реестр проб и образцов ({{ samples.length }})</h3>
      <table>
        <thead>
          <tr>
            <th>Код пробы</th>
            <th>Тип материала</th>
            <th>Тип пробы</th>
            <th>Кат. № ВИР</th>
            <th>Вид</th>
            <th>Место хранения</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in samples" :key="s.id">
            <td><strong>{{ s.code }}</strong></td>
            <td>{{ s.kind }}</td>
            <td>{{ s.sample_type }}</td>
            <td>{{ s.vir_number }}</td>
            <td>{{ s.species }}</td>
            <td>{{ s.storage_place }}</td>
          </tr>
          <tr v-if="!samples.length"><td colspan="6">Образцы пока не зарегистрированы</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { fetchApi } from '../../services/api'

const samples = ref([])
const blocks = ref([])
onMounted(async () => {
  try {
    const data = await fetchApi('/api/samples/')
    samples.value = data.samples || []
    blocks.value = data.blocks || []
  } catch (e) {
    console.warn(e)
  }
})
</script>

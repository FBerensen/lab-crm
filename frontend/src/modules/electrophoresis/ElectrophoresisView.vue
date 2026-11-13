<template>
  <div>
    <h1>⚡ Форезы (Гель-электрофорез и контроль качества ДНК/ПЦР)</h1>
    <p style="color: #64748b;">Папка разработчика: <code>frontend/src/modules/electrophoresis/</code> ↔ <code>lab/domains/electrophoresis/</code></p>

    <div class="card" v-for="gel in gels" :key="gel.id">
      <h3>Форез #{{ gel.id }} · {{ gel.actual_day }} · Агароза {{ gel.agarose_percent }}% ({{ gel.voltage_v }} В, {{ gel.duration_min }} мин)</h3>
      <p style="color: #475569; font-size: 13px;">
        Задача: <strong>{{ gel.task }}</strong> | Выполнение методики: {{ gel.laboratory_run }} | Партия буфера: {{ gel.buffer_lot }} | Маркер: {{ gel.marker_name }}
      </p>
      <table>
        <thead>
          <tr>
            <th>Дорожка №</th>
            <th>Проба / Контроль</th>
            <th>Объём, мкл</th>
            <th>Размер фрагмента, п.н.</th>
            <th>Оценка дорожки</th>
            <th>Примечание</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="lane in gel.lanes" :key="lane.lane_number">
            <td><strong>#{{ lane.lane_number }}</strong></td>
            <td>{{ lane.sample }}</td>
            <td>{{ lane.sample_volume_ul }} мкл</td>
            <td>{{ lane.band_size_bp }}</td>
            <td>{{ lane.quality }}</td>
            <td>{{ lane.note || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="card" v-if="!gels.length">
      <h3>Журнал гель-электрофореза</h3>
      <p>В базе пока нет записей форезов. Вы можете добавить гель и дорожки через раздел «Форезы (электрофорез)» в админке или API.</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { fetchApi } from '../../services/api'

const gels = ref([])
onMounted(async () => {
  try {
    const data = await fetchApi('/api/electrophoresis/')
    gels.value = data.gels || []
  } catch (e) {
    console.warn(e)
  }
})
</script>

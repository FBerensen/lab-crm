<template>
  <div>
    <h1>📋 Задачи и журнал работ</h1>
    <p style="color: #64748b;">Папка разработчика: <code>frontend/src/modules/worklog/</code> ↔ <code>lab/domains/worklog/</code></p>

    <div class="card">
      <h3>Текущие задачи ({{ tasks.length }})</h3>
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Задача</th>
            <th>Проект</th>
            <th>Исполнитель</th>
            <th>Проверяющий (Руководитель)</th>
            <th>Этап</th>
            <th>Состояние</th>
            <th>Срок</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.id">
            <td>#{{ t.id }}</td>
            <td><strong>{{ t.title }}</strong></td>
            <td>{{ t.project }}</td>
            <td>{{ t.owner }}</td>
            <td>{{ t.reviewer }}</td>
            <td>{{ t.stage }}</td>
            <td><strong>{{ t.status }}</strong></td>
            <td>{{ t.due }}</td>
          </tr>
          <tr v-if="!tasks.length"><td colspan="8">Задачи пока не созданы</td></tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3>Журнал выполненных работ ({{ worklogs.length }})</h3>
      <table>
        <thead>
          <tr>
            <th>Дата работы</th>
            <th>Сотрудник</th>
            <th>Задача</th>
            <th>Выполненная операция</th>
            <th>Результат / Подробности</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="w in worklogs" :key="w.id">
            <td>{{ w.actual_day }}</td>
            <td>{{ w.actor }}</td>
            <td>{{ w.task }}</td>
            <td><strong>{{ w.operation }}</strong></td>
            <td>{{ w.detail }}</td>
          </tr>
          <tr v-if="!worklogs.length"><td colspan="5">Записей журнала пока нет</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { fetchApi } from '../../services/api'

const tasks = ref([])
const worklogs = ref([])
onMounted(async () => {
  try {
    const data = await fetchApi('/api/worklog/')
    tasks.value = data.tasks || []
    worklogs.value = data.worklogs || []
  } catch (e) {
    console.warn(e)
  }
})
</script>

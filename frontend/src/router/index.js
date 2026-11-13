import { createRouter, createWebHistory } from 'vue-router'
import WarehouseView from '../modules/warehouse/WarehouseView.vue'
import SolutionsView from '../modules/solutions/SolutionsView.vue'
import DnaExtractionView from '../modules/dna_extraction/DnaExtractionView.vue'
import ElectrophoresisView from '../modules/electrophoresis/ElectrophoresisView.vue'
import SamplesView from '../modules/samples/SamplesView.vue'
import WorklogView from '../modules/worklog/WorklogView.vue'

const routes = [
  { path: '/', redirect: '/warehouse' },
  { path: '/warehouse', name: 'warehouse', component: WarehouseView, meta: { title: 'Склад и хранение' } },
  { path: '/solutions', name: 'solutions', component: SolutionsView, meta: { title: 'Приготовление растворов' } },
  { path: '/dna-extraction', name: 'dna_extraction', component: DnaExtractionView, meta: { title: 'Выделение ДНК и ПЦР' } },
  { path: '/electrophoresis', name: 'electrophoresis', component: ElectrophoresisView, meta: { title: 'Форезы (Электрофорез)' } },
  { path: '/samples', name: 'samples', component: SamplesView, meta: { title: 'Образцы и блоки A1–H12' } },
  { path: '/worklog', name: 'worklog', component: WorklogView, meta: { title: 'Задачи и журнал работ' } }
]

export default createRouter({
  history: createWebHistory(),
  routes
})

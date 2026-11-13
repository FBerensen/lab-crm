# 🧬 LabCRM — Лабораторная CRM (Django 5.2 + Vue 3)

Чистая модульная архитектура лабораторной CRM для молекулярной селекции и ДНК-паспортизации растений. Проект разделён по канонам чистого кода (по образцу `Foodgram`) на независимые папки, чтобы несколько разработчиков могли параллельно работать над своими отделами без конфликтов.

---

## 📁 Структура проекта

### Корень репозитория
```text
lab-crm/
├── .github/workflows/   # CI/CD пайплайн (тесты Django + сборка Vue 3)
├── backend/             # Серверная часть (Django 5.2, разложена по отделам лаборатории)
├── docs/                # Подробная документация и история этапов 1–8З
├── frontend/            # Клиентская часть (Vue 3 + Vite + Pinia + Vue Router)
├── infra/               # Docker Compose, разделённые .env файлы и скрипт БД
├── .gitignore
├── README.md
└── setup.cfg            # Настройки линтера flake8 и pytest
```

### Внутри `backend/` (каждый отдел лаборатории — в своей папке)
Никакой свалки из десятков файлов в одном каталоге. У каждого направления — своя папка с чистыми `models.py`, `admin.py`, `services.py` и `tests/`:

```text
backend/
├── api/                 # JSON REST API эндпоинты для связи с Vue 3
├── backend/             # Конфигурация проекта (settings.py, urls.py, wsgi.py)
├── warehouse/           # 📦 Склад, партии реактивов, места хранения (4–8З)
├── solutions/           # 🧪 Приготовление растворов, калькулятор, черновики, автоклав
├── dna_extraction/      # 🧬 Выделение ДНК, методики ПЦР и планы материалов
├── electrophoresis/     # ⚡ Форезы (агарозные гели, дорожки, маркеры, контроль качества)
├── samples/             # 🌱 Образцы, паспорта ВИР и планшеты 96 лунок (A1–H12)
├── worklog/             # 📋 Проекты, задачи, журнал работ и квартальные отчёты
├── users/               # 👥 Роли (Руководители, Склад, Исполнители, Стажёры) и права
├── lab/                 # ⚙️ Ядро миграций БД (0001–0024), статика и шаблоны
├── Dockerfile
├── manage.py
└── requirements.txt
```

### Внутри `frontend/` (Vue 3 SPA)
```text
frontend/
├── public/
├── src/
│   ├── modules/         # Зеркальные папки отделов для параллельной работы
│   │   ├── warehouse/
│   │   ├── solutions/
│   │   ├── dna_extraction/
│   │   ├── electrophoresis/
│   │   ├── samples/
│   │   └── worklog/
│   ├── router/
│   ├── services/
│   ├── App.vue
│   └── main.js
├── Dockerfile
├── index.html
├── package.json
└── vite.config.js
```

---

## 🚀 Быстрый запуск на обычном компьютере (без Docker)

По умолчанию проект настроен на локальную базу `SQLite`, поэтому запускается на любом компьютере без установки PostgreSQL.

### 1. Запуск Django Backend (Терминал 1)
```bash
cd backend
python -m venv .venv
source .venv/bin/activate      # На Windows: .venv\Scripts\activate
pip install -r requirements.txt

python manage.py migrate
python manage.py bootstrap
python manage.py createsuperuser
python manage.py runserver
```
* Админ-панель и API откроются на `http://127.0.0.1:8000/admin/`

### 2. Запуск Vue 3 Frontend (Терминал 2)
```bash
cd frontend
npm install
npm run dev
```
* Интерфейс Vue 3 откроется на `http://localhost:5173/`

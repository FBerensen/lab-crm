(function () {
    "use strict";
    function init() {
        const task = document.getElementById("id_task");
        const samples = document.getElementById("id_samples");
        if (!task || !samples || !samples.dataset.worklogSamplesUrl) return;
        let sequence = 0;
        let blocked = false;
        if (samples.form) samples.form.addEventListener("submit", function (event) {
            if (blocked) {
                event.preventDefault();
                window.alert("Дождитесь загрузки проб задачи. При ошибке обновите страницу.");
            }
        });
        task.addEventListener("change", async function () {
            const current = ++sequence;
            const selected = new Set(Array.from(samples.selectedOptions).map(o => o.value));
            blocked = true;
            samples.replaceChildren();
            samples.disabled = true;
            try {
                const response = await fetch(samples.dataset.worklogSamplesUrl + "?task=" + encodeURIComponent(task.value), {credentials: "same-origin"});
                if (!response.ok) throw new Error("load failed");
                const data = await response.json();
                if (current !== sequence) return;
                for (const item of data.results) samples.add(new Option(item.text, String(item.id), false, selected.has(String(item.id))));
                blocked = false;
            } catch (error) {
                if (current === sequence) window.alert("Не удалось загрузить пробы задачи. Обновите страницу перед сохранением.");
            } finally {
                if (current === sequence) samples.disabled = false;
            }
        });
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();

(function(){"use strict";
function init(){
 const task=document.getElementById("id_task"),run=document.getElementById("id_laboratory_run"),samples=document.getElementById("id_samples");
 if(!task||!run||!samples||!run.dataset.runsUrl)return;
 let seq=0,blocked=false;
 if(run.form)run.form.addEventListener("submit",function(e){if(blocked){e.preventDefault();window.alert("Дождитесь загрузки выполнения и проб. При ошибке обновите страницу.");}});
 async function load(url){const r=await fetch(url,{credentials:"same-origin"});if(!r.ok)throw new Error("load");return(await r.json()).results;}
 task.addEventListener("change",async function(){const id=++seq;blocked=true;run.disabled=true;run.replaceChildren(new Option("---------",""));
  try{const rows=await load(run.dataset.runsUrl+"?task="+encodeURIComponent(task.value));if(id!==seq)return;for(const r of rows)run.add(new Option(r.text,String(r.id)));blocked=false;}
  catch(e){if(id===seq)window.alert("Не удалось загрузить выполнения. Обновите страницу.");}
  finally{if(id===seq)run.disabled=false;}
 });
 run.addEventListener("change",async function(){const id=++seq;blocked=true;
  try{for(let n=0;samples.disabled&&n<100;n++)await new Promise(r=>setTimeout(r,50));if(samples.disabled)throw new Error("busy");
   const selected=new Set(Array.from(samples.selectedOptions).map(o=>o.value));
   const url=run.value?run.dataset.runSamplesUrl+"?run="+encodeURIComponent(run.value):samples.dataset.worklogSamplesUrl+"?task="+encodeURIComponent(task.value);
   const rows=await load(url);if(id!==seq)return;samples.replaceChildren();for(const r of rows)samples.add(new Option(r.text,String(r.id),false,selected.has(String(r.id))));blocked=false;
  }catch(e){if(id===seq)window.alert("Не удалось загрузить пробы выполнения. Обновите страницу.");}
 });
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init);else init();
})();

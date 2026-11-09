document.querySelectorAll('time[data-utc]').forEach(t=>{const d=new Date(t.dataset.utc);if(!isNaN(d))t.textContent=d.toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'})});
if(document.querySelector('.st.processing,.st.queued'))setTimeout(()=>location.reload(),8000);

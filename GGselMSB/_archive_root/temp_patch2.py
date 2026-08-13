import sys

app_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/static/app.js"
with open(app_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update loaders
if "'promo-codes':      loadPromoCodes," not in content:
    content = content.replace("'dashboard':      loadDashboard,", "'dashboard':      loadDashboard,\n    'promo-codes':      loadPromoCodes,")

# 2. Add loadPromoCodes implementation at the end of the file
new_js = '''
// ═══════════════════════════════════════════════════
//  PROMO CODES (Stage 2)
// ═══════════════════════════════════════════════════
async function loadPromoCodes() {
  const tbody = document.getElementById('promo-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="6" class="text-center" style="padding:20px;">Загрузка...</td></tr>';
  try {
    const data = await api('/api/promo_codes');
    renderPromoCodes(data);
  } catch (e) {
    console.error(e);
    tbody.innerHTML = '<tr><td colspan="6" class="text-center" style="color:var(--red);">Ошибка загрузки промокодов</td></tr>';
  }
}

function renderPromoCodes(data) {
  const tbody = document.getElementById('promo-tbody');
  if (!tbody) return;
  
  if (!data || !data.data || data.data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted" style="padding:20px;">Нет промокодов</td></tr>';
    return;
  }
  
  tbody.innerHTML = data.data.map(p => {
    let dateStr = 'Безлимитный';
    if (p.start_date || p.end_date) {
        dateStr = ${p.start_date ? new Date(p.start_date).toLocaleDateString() : '—'} - ;
    }
    const status = p.active ? '<span style="color:var(--green)">Активен</span>' : '<span style="color:var(--text-muted)">Неактивен</span>';
    
    return 
      <tr>
        <td style="font-family:monospace; color:var(--primary); font-weight:600;"></td>
        <td></td>
        <td style="font-size:12px; color:var(--text-dim);"></td>
        <td> / </td>
        <td></td>
        <td class="text-right">
          <button class="btn btn-sm btn-icon" title="Удалить" style="color:var(--text-muted);"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg></button>
        </td>
      </tr>
    ;
  }).join('');
}
'''

if "async function loadPromoCodes" not in content:
    content += new_js
    
with open(app_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Added loadPromoCodes.")

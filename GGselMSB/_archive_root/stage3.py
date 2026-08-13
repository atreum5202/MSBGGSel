import sys

# 1. Update app.py
app_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/app.py"
with open(app_path, "r", encoding="utf-8") as f:
    app_content = f.read()

new_endpoints = '''
# ==========================================
# STAGE 3: WHOLESALE (Cookie-based Auth)
# ==========================================
@app.route("/api/wholesale", methods=["GET"])
def get_wholesale():
    url = "https://seller.ggsel.com/api_sellers/api/wholesale"
    status, data = _cookie_get(url, params=request.args)
    return jsonify(data), status

@app.route("/api/wholesale/filters", methods=["GET"])
def get_wholesale_filters():
    url = "https://seller.ggsel.com/api_sellers/api/wholesale/filters"
    status, data = _cookie_get(url)
    return jsonify(data), status
'''

if "STAGE 3: WHOLESALE" not in app_content:
    if 'if __name__ == "__main__":' in app_content:
        app_content = app_content.replace('if __name__ == "__main__":', new_endpoints + '\nif __name__ == "__main__":')
    elif "if __name__ == '__main__':" in app_content:
        app_content = app_content.replace("if __name__ == '__main__':", new_endpoints + "\nif __name__ == '__main__':")
    else:
        app_content += new_endpoints
    with open(app_path, "w", encoding="utf-8") as f:
        f.write(app_content)

# 2. Update index.html
idx_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/templates/index.html"
with open(idx_path, "r", encoding="utf-8") as f:
    idx_content = f.read()

new_section = '''
<!-- STAGE 3: WHOLESALE -->
<section class="view" id="view-wholesale">
  <div class="page-head">
    <div>
      <div class="page-title">Оптовые закупки</div>
      <div class="page-sub">Управление скидками для оптовиков</div>
    </div>
    <div class="page-actions">
      <button class="btn btn-primary">+ Добавить</button>
    </div>
  </div>
  <div class="card" style="padding: 0;">
    <table class="table" style="margin-bottom:0;">
      <thead>
        <tr>
          <th>Пользователь</th>
          <th>Товар</th>
          <th>Скидка</th>
          <th style="width:50px;"></th>
        </tr>
      </thead>
      <tbody id="wholesale-tbody">
      </tbody>
    </table>
  </div>
</section>
'''

if 'id="view-wholesale"' not in idx_content:
    # insert before </main>
    idx_content = idx_content.replace('</main>', new_section + '\n    </main>')
    with open(idx_path, "w", encoding="utf-8") as f:
        f.write(idx_content)

# 3. Update app.js
js_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/static/app.js"
with open(js_path, "r", encoding="utf-8") as f:
    js_content = f.read()

new_js = '''
// ═══════════════════════════════════════════════════
//  WHOLESALE (Stage 3)
// ═══════════════════════════════════════════════════
async function loadWholesale() {
  const tbody = document.getElementById('wholesale-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="4" class="text-center" style="padding:20px;">Загрузка...</td></tr>';
  try {
    const data = await api('/api/wholesale');
    renderWholesale(data);
  } catch (e) {
    console.error(e);
    tbody.innerHTML = '<tr><td colspan="4" class="text-center" style="color:var(--red);">Ошибка загрузки оптовых закупок</td></tr>';
  }
}

function renderWholesale(data) {
  const tbody = document.getElementById('wholesale-tbody');
  if (!tbody) return;
  if (!data || !data.data || data.data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted" style="padding:20px;">Нет данных об оптовых закупках</td></tr>';
    return;
  }
  tbody.innerHTML = data.data.map(w => {
    return 
      <tr>
        <td style="font-weight:600;"></td>
        <td></td>
        <td>%</td>
        <td class="text-right">
          <button class="btn btn-sm btn-icon" title="Удалить" style="color:var(--text-muted);"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg></button>
        </td>
      </tr>
    ;
  }).join('');
}
'''
if "'wholesale':      loadWholesale," not in js_content:
    js_content = js_content.replace("'promo-codes':      loadPromoCodes,", "'promo-codes':      loadPromoCodes,\n    'wholesale':      loadWholesale,")

if "async function loadWholesale" not in js_content:
    js_content += new_js
    with open(js_path, "w", encoding="utf-8") as f:
        f.write(js_content)

print("Stage 3 wholesale files updated successfully.")

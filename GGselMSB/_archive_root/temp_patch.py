import sys

app_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/templates/index.html"
with open(app_path, "r", encoding="utf-8") as f:
    content = f.read()

start_marker = '<section class="view" id="view-promo-codes">'
end_marker = '</section>'

start_idx = content.find(start_marker)
if start_idx == -1:
    print("Section not found!")
    sys.exit(1)

end_idx = content.find(end_marker, start_idx) + len(end_marker)

new_section = '''<section class="view" id="view-promo-codes">
  <div class="page-head">
    <div>
      <div class="page-title">Промокоды</div>
      <div class="page-sub">Управление промокодами для ваших товаров</div>
    </div>
    <div class="page-actions">
      <button class="btn btn-primary">+ Добавить</button>
    </div>
  </div>

  <div class="filters-bar" style="display:flex; gap:10px; margin-bottom: 20px;">
    <select class="select" id="promo-status-filter" style="width:150px;">
      <option value="">Все статусы</option>
    </select>
    <select class="select" id="promo-offer-filter" style="width:200px;">
      <option value="">Все товары</option>
    </select>
    <input type="text" class="inp" id="promo-search-inp" placeholder="Поиск по коду..." style="width:250px;">
    <div style="flex:1;"></div>
    <button class="btn" id="promo-search-btn">Найти</button>
  </div>

  <div class="card" style="padding: 0;">
    <table class="table" style="margin-bottom:0;">
      <thead>
        <tr>
          <th>Код</th>
          <th>Скидка</th>
          <th>Период</th>
          <th>Активаций</th>
          <th>Статус</th>
          <th style="width:50px;"></th>
        </tr>
      </thead>
      <tbody id="promo-tbody">
        <!-- JS fill -->
      </tbody>
    </table>
  </div>
  
  <div class="pagination" id="promo-pagination" style="display:flex; justify-content:center; gap:5px; margin-top:20px;">
    <!-- JS fill -->
  </div>
</section>'''

content = content[:start_idx] + new_section + content[end_idx:]

with open(app_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Replaced promo-codes section.")

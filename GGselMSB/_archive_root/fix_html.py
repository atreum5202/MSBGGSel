import sys

app_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/templates/index.html"
with open(app_path, "r", encoding="utf-8") as f:
    content = f.read()

# Replace sidebar link
content = content.replace('data-view="help"', 'data-view="docs"')

# Replace #view-help section if it exists, otherwise just append it
start_marker = '<section class="view" id="view-help">'
end_marker = '</section>'

new_docs_html = '''<section class="view" id="view-docs">
  <div class="page-head">
    <div class="page-title">Справка</div>
    <div class="page-sub">Документация по API GGSEL</div>
  </div>
  <div class="card">
    <div class="docs-list">
      <a class="docs-link" href="https://seller.ggsel.com/docs" target="_blank">Введение ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/seller-api-v-1" target="_blank">API v1 ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/v2/seller-api-v-2" target="_blank">API v2 ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/v2/list-of-categories" target="_blank">Список категорий v2 ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/v2/search-categories" target="_blank">Поиск категорий v2 ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/v2/view-option" target="_blank">Просмотр опции v2 ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/v2/list-offer-options-visible-to-seller" target="_blank">Список опций продавца v2 ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/v2/create-many" target="_blank">Создание многих v2 ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/v2/get-async-job-result" target="_blank">Результат задачи v2 ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/return-seller-balance-info" target="_blank">Баланс продавца ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/return-seller-receipts" target="_blank">Чеки продавца ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/return-seller-token" target="_blank">Токен продавца ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/return-all-categories" target="_blank">Все категории ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/create-message-without-file" target="_blank">Сообщение без файла ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/list-of-chats" target="_blank">Список чатов ↗</a>
      <a class="docs-link" href="https://seller.ggsel.com/docs/list-of-messages" target="_blank">Список сообщений ↗</a>
    </div>
  </div>
</section>'''

start_idx = content.find(start_marker)
if start_idx != -1:
    end_idx = content.find(end_marker, start_idx) + len(end_marker)
    content = content[:start_idx] + new_docs_html + content[end_idx:]
else:
    # If not found, check if view-docs already exists
    if 'id="view-docs"' not in content:
        content = content.replace('</main>', new_docs_html + '\n    </main>')

with open(app_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated index.html for docs")

css_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/static/style.css"
with open(css_path, "r", encoding="utf-8") as f:
    css_content = f.read()

if ".docs-link" not in css_content:
    new_css = '''
.docs-link {
  display: block;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-soft);
  color: var(--text);
  text-decoration: none;
  transition: background 0.2s;
}
.docs-link:hover {
  background: var(--bg-elevated);
  color: var(--primary);
}
.docs-list {
  display: flex;
  flex-direction: column;
}
'''
    with open(css_path, "a", encoding="utf-8") as f:
        f.write("\n" + new_css)
    print("Added docs CSS")

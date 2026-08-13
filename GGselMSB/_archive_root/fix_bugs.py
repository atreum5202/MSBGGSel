import sys

js_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/static/app.js"
with open(js_path, "r", encoding="utf-8") as f:
    content = f.read()

# Bug 1
content = content.replace(
    "let q = ?days=;\n  if (statusEl && statusEl.value) q += &status=;\n  if (searchEl && searchEl.value) q += &search=;\n  \n  try {\n    const data = await api(/api/sales);",
    "const params = new URLSearchParams();\n  params.set('limit', '50');\n  if (days) params.set('days', days);\n  if (statusEl && statusEl.value) params.set('status', statusEl.value);\n  if (searchEl && searchEl.value) params.set('q', searchEl.value);\n  \n  try {\n    const data = await api('/api/sales?' + params.toString());"
)

# Bug 2
content = content.replace(
    'const activeTab = document.querySelector(.wizard-tab[data-step=""]);',
    'const activeTab = document.querySelector(.wizard-tab[data-step=""]);'
)

# Bug 3
content = content.replace(
    'const activeTab = document.querySelector(.wizard-tab[data-ptab=""]);',
    'const activeTab = document.querySelector(.wizard-tab[data-ptab=""]);'
)

# Bug 4
content = content.replace(
    'const activeTab = document.querySelector(.wizard-tab[data-stab=""]);',
    'const activeTab = document.querySelector(.wizard-tab[data-stab=""]);'
)

# Stage 9 VIEWS change
if "'docs': () => {}," not in content:
    content = content.replace("'help': () => {},", "'docs': () => {},")
    content = content.replace("'help',", "'docs',")

with open(js_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Fixed bugs in app.js")

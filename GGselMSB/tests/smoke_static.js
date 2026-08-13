const fs = require("fs");
const path = require("path");
const vm = require("vm");

// JS syntax check via vm — runs in isolated context (no DOM, no $ collisions)
const js = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf-8");
try {
  new vm.Script(js, { filename: "app.js" });
  console.log("OK app.js: " + js.length + " chars, " + js.split("\n").length + " lines (syntax OK)");
} catch (e) {
  console.log("FAIL app.js syntax:", e.message);
  process.exit(1);
}

// CSS basic check
const css = fs.readFileSync(path.join(__dirname, "..", "static", "style.css"), "utf-8");
console.log("OK style.css: " + css.length + " chars, " + css.split("\n").length + " lines");
const hasFormGrid = css.includes(".form-grid");
console.log("  has .form-grid: " + hasFormGrid);

// HTML basic check
const html = fs.readFileSync(path.join(__dirname, "..", "templates", "index.html"), "utf-8");
console.log("OK index.html: " + html.length + " chars, " + html.split("\n").length + " lines");
const views = (html.match(/<section class="view[^"]*"/g) || []).length;
const navItems = (html.match(/data-view="/g) || []).length;
const parserView = html.includes('id="view-parser"');
const parserNav = html.includes('data-view="parser"');
console.log("  views: " + views);
console.log("  nav items: " + navItems);
console.log("  has #view-parser section: " + parserView);
console.log("  has nav-item data-view=parser: " + parserNav);

// All parser buttons present
const btns = [
  "btn-parser-start", "btn-parser-stop", "btn-parser-stats",
  "btn-parser-refresh", "btn-parser-clear-form",
  "parser-input-query", "parser-input-category",
  "parser-input-quantity", "parser-input-pages", "parser-input-ai",
  "parser-products-search", "parser-products-status",
  "parser-status-text", "parser-stat-saved", "parser-stat-ai",
  "parser-stat-pages", "parser-stat-errors", "parser-last-run",
  "parser-action-status", "parser-products-table", "parser-runs-table",
  "parser-log-card", "parser-log-entries",
];
let missing = [];
for (const id of btns) {
  if (!html.includes('id="' + id + '"')) missing.push(id);
}
if (missing.length === 0) {
  console.log("  all " + btns.length + " parser UI elements present");
} else {
  console.log("  MISSING UI elements: " + missing.join(", "));
  process.exit(1);
}

console.log();
console.log("All static asset smoke tests passed");

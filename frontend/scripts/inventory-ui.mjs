// Enumerate every native form control, including conditional editor branches.
import ts from 'typescript';
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join, relative } from 'node:path';
const root = new URL('../src/', import.meta.url).pathname;
const fields = [];
function walk(dir) { for (const item of readdirSync(dir, { withFileTypes: true })) { const file = join(dir, item.name); if (item.isDirectory()) walk(file); else if (file.endsWith('.tsx')) scan(file); } }
function scan(file) {
  const source = ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  function attr(node, name) { return node.attributes?.properties.find((a) => ts.isJsxAttribute(a) && a.name.text === name)?.initializer?.getText(source) ?? ''; }
  function visit(node) {
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && ['input', 'select', 'textarea'].includes(node.tagName.getText(source))) {
      let parent = node.parent, field, wrapped = false;
      while (parent) { if (ts.isJsxElement(parent)) { const opening = parent.openingElement; if (opening.tagName.getText(source) === 'Field') { field = opening; break; } if (opening.tagName.getText(source) === 'label') wrapped = true; } parent = parent.parent; }
      const entry = { file: relative(root, file), line: source.getLineAndCharacterOfPosition(node.getStart()).line + 1, control: node.tagName.getText(source), type: attr(node, 'type'), id: attr(node, 'id'), label: field ? attr(field, 'labelKey') : wrapped ? 'enclosing label' : attr(node, 'aria-label'), hint: field ? attr(field, 'hint') : '', placeholder: attr(node, 'placeholder') };
      // Two filter selects use explicit label/htmlFor, checked in browser review.
      if (!entry.label && ['"audit-action"', '"notification-status"'].includes(entry.id)) entry.label = 'label/htmlFor';
      fields.push(entry);
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
}
walk(root);
const missing = fields.filter((field) => !field.label);
writeFileSync(new URL('../../docs/ui-field-inventory.json', import.meta.url), JSON.stringify({ controls: fields.length, files: new Set(fields.map((field) => field.file)).size, fields }, null, 2) + '\n');
if (missing.length) { console.error(missing); process.exitCode = 1; }
console.log(`${fields.length} controls in ${new Set(fields.map((field) => field.file)).size} files; ${missing.length} without label references.`);

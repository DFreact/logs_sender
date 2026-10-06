import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

const violations = [];
const visibleAttributes = new Set(['title', 'placeholder', 'aria-label', 'aria-description',
  'alt', 'label', 'helperText', 'message', 'description', 'children']);

export function inspectSource(source, filename) {
  const errors = [];
  const tree = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true,
    filename.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const report = (node, reason) => errors.push(`${filename}:${tree.getLineAndCharacterOfPosition(node.getStart()).line + 1}: ${reason}`);
  function visibleExpression(node) {
    if (ts.isStringLiteralLike(node) || ts.isTemplateExpression(node)) report(node, 'UI_LITERAL');
    else if (ts.isConditionalExpression(node)) { visibleExpression(node.whenTrue); visibleExpression(node.whenFalse); }
    else if (ts.isBinaryExpression(node)) {
      const operator = node.operatorToken.kind;
      if ([ts.SyntaxKind.EqualsEqualsToken, ts.SyntaxKind.EqualsEqualsEqualsToken,
        ts.SyntaxKind.ExclamationEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken,
        ts.SyntaxKind.LessThanToken, ts.SyntaxKind.GreaterThanToken,
        ts.SyntaxKind.LessThanEqualsToken, ts.SyntaxKind.GreaterThanEqualsToken].includes(operator)) return;
      // The left operand of && controls rendering; it is never the displayed string.
      if (operator !== ts.SyntaxKind.AmpersandAmpersandToken) visibleExpression(node.left);
      visibleExpression(node.right);
    }
    else if (ts.isParenthesizedExpression(node)) visibleExpression(node.expression);
  }
  function walk(node) {
    if (ts.isJsxText(node) && node.text.trim()) report(node, 'JSX_TEXT');
    if (ts.isJsxExpression(node) && node.expression && !ts.isJsxAttribute(node.parent)) visibleExpression(node.expression);
    if (ts.isJsxAttribute(node)) {
      if (node.name.getText() === 'dangerouslySetInnerHTML') report(node, 'RAW_HTML');
      if (visibleAttributes.has(node.name.getText()) && node.initializer) {
        if (ts.isStringLiteral(node.initializer) && node.initializer.text.trim()) report(node, 'UI_ATTRIBUTE');
        if (ts.isJsxExpression(node.initializer) && node.initializer.expression) visibleExpression(node.initializer.expression);
      }
    }
    if (ts.isStringLiteralLike(node) && /[А-Яа-яЁё]/.test(node.text)) report(node, 'TEXT_OUTSIDE_CATALOG');
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)
      && /i18n\/locales/.test(node.moduleSpecifier.text)) report(node, 'DIRECT_CATALOG_IMPORT');
    if (ts.isCallExpression(node) && /^(toast|alert|confirm|prompt|console\.(log|warn|error))(\.|$)/.test(node.expression.getText())) {
      for (const argument of node.arguments) visibleExpression(argument);
    }
    ts.forEachChild(node, walk);
  }
  walk(tree);
  return errors;
}

function scan(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) { if (entry.name !== 'i18n') scan(file); }
    else if (/\.tsx?$/.test(entry.name)) violations.push(...inspectSource(fs.readFileSync(file, 'utf8'), file));
  }
}
if (process.argv[1]?.endsWith('check-ui-language.mjs')) {
  scan('src');
  if (violations.length) { console.error(violations.join('\n')); process.exitCode = 1; }
  else console.log('UI language check passed.');
}

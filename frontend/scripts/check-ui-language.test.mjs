import { test } from 'node:test';
import assert from 'node:assert/strict';
import { inspectSource } from './check-ui-language.mjs';

test('rejects untranslated content, attributes and messages', () => {
  for (const code of ['<button>Save</button>', '<button>{"Save"}</button>',
    '<input placeholder="Search" />', '<img alt={failed ? "Error" : "Ready"} />',
    'toast.error("Delivery failed")', 'const title = "События"']) {
    assert.ok(inspectSource(code, 'sample.tsx').length > 0, code);
  }
});
test('allows translated text and technical attributes', () => {
  const code = '<button className={active ? "active" : "idle"} title={t("common.save")}>{t("common.save")}</button>';
  assert.deepEqual(inspectSource(code, 'sample.tsx'), []);
});
test('allows enum guards but rejects visible text in logical expressions', () => {
  assert.deepEqual(inspectSource('<p>{kind === "NOTIFY" && t("routing.mode")}</p>', 'sample.tsx'), []);
  for (const code of ['<p>{enabled && "Send"}</p>', '<p>{title || "Fallback"}</p>', '<p>{title ?? "Fallback"}</p>']) {
    assert.ok(inspectSource(code, 'sample.tsx').length > 0, code);
  }
});

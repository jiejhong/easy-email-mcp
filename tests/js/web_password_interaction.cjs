// Execute the actual inline UI JavaScript with a minimal DOM and mocked fetch.
// No real account, keyring, mailbox, browser storage or network is used.
const vm = require('node:vm');
const assert = require('node:assert/strict');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { input += chunk; });
process.stdin.on('end', async () => {
  const source = JSON.parse(input).script;
  const config = {
    imap_host: 'imap.example.invalid', imap_port: 993,
    smtp_host: 'smtp.example.invalid', smtp_port: 465,
    smtp_security: 'tls', username: 'fixture@example.invalid',
    secret_source: 'keyring', secret_env: 'EASY_EMAIL_PASSWORD',
    send_enabled: false, recipients: [], state_dir: '',
    max_message_bytes: 8388608, max_attachment_bytes: 2097152, timeout: 20,
  };
  async function setup(load = {}) {
    const buttons = [{ disabled: false }, { disabled: false }];
    const form = { elements: { password: { value: 'TEST_ONLY_NOT_A_CREDENTIAL' } },
      addEventListener(event, fn) { assert.equal(event, 'submit'); this.submit = fn; },
      querySelectorAll() { return buttons; } };
    const result = { textContent: '' };
    const labels = [];
    const fields = { append(label) {
      labels.push(label);
      for (const child of label.children) if (child.name) form.elements[child.name] = child;
    } };
    const requests = [], responses = [];
    const document = {
      querySelector(selector) { return selector === 'form' ? form : selector === '#fields' ? fields : result; },
      createElement() { return { children: [], setAttribute(name, value) { this[name] = value; }, append(child) { this.children.push(child); } }; },
    };
    class FormData {
      constructor() { this.rows = Object.entries(form.elements).map(([key, value]) => [key, value.value]); }
      [Symbol.iterator]() { return this.rows[Symbol.iterator](); }
    }
    async function fetch(url, options) {
      if (!options) {
        if (load.reject) throw Error('load failure');
        if (load.pending) await load.pending;
        return { ok: load.ok !== false, json: async () => {
          if (load.invalidJson) throw Error('invalid JSON');
          return load.body || { ...config };
        } };
      }
      requests.push({ url, data: JSON.parse(options.body) });
      const response = responses.shift();
      assert.ok(response, 'Every POST needs an explicit mock response');
      if (response.pending) await response.pending;
      if (response.edit) form.elements.password.value = response.edit;
      if (response.reject) throw new Error('mock network failure');
      return { ok: response.ok !== false, json: async () => {
        if (response.invalidJson) throw new Error('mock invalid JSON');
        return response.body;
      } };
    }
    vm.runInNewContext(source, { document, FormData, fetch });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(typeof form.submit, 'function');
    return { form, result, requests, responses, buttons, labels,
      submit: (value) => form.submit({ preventDefault() {}, submitter: { value } }) };
  }
  let checked = 0;
  // Successful test must preserve the input for a subsequent save request.
  {
    const ui = await setup();
    const original = ui.form.elements.password.value;
    ui.responses.push({ body: { imap: 'authenticated', smtp: 'authenticated' } });
    await ui.submit('test');
    assert.equal(ui.form.elements.password.value, original);
    ui.responses.push({ body: { saved: true, message: 'saved' } });
    await ui.submit('save');
    assert.deepEqual(ui.requests.map(x => x.url), ['/api/test', '/api/config']);
    assert.equal(ui.requests[1].data.password, original);
    assert.equal(ui.form.elements.password.value, '');
    checked++;
  }
  for (const action of ['test', 'save']) {
    for (const response of [
      { ok: false, body: { error: 'invalid settings' } },
      { ok: false, body: { partial: true, error: 'config commit failed' } },
      { reject: true },
      { invalidJson: true },
      { body: { saved: false } },
      { ok: false, body: { saved: true } },
    ]) {
      const ui = await setup(), original = ui.form.elements.password.value;
      ui.responses.push(response); await ui.submit(action);
      assert.equal(ui.form.elements.password.value, original, `${action} failure must retain input`);
      checked++;
    }
  }
  // Protocol authentication failure is a successful HTTP result, still not a save.
  {
    const ui = await setup(), original = ui.form.elements.password.value;
    ui.responses.push({ body: { imap: 'failed', smtp: 'failed' } }); await ui.submit('test');
    assert.equal(ui.form.elements.password.value, original); checked++;
  }
  // Never clear a newly edited password on completion of an older save request.
  {
    const ui = await setup();
    ui.responses.push({ edit: 'NEW_TEST_ONLY_INPUT', body: { saved: true } }); await ui.submit('save');
    assert.equal(ui.form.elements.password.value, 'NEW_TEST_ONLY_INPUT'); checked++;
  }
  // Both persisted values render clearly and submit actual JSON booleans.
  for (const enabled of [false, true]) {
    const ui = await setup({ body: { ...config, send_enabled: enabled } });
    const select = ui.form.elements.send_enabled;
    assert.equal(select.value, String(enabled));
    assert.equal(ui.labels[0].textContent, '发送权限默认状态');
    assert.deepEqual(select.children.map(o => o.textContent), ['默认关闭（推荐）', '默认打开']);
    assert.equal(select['aria-describedby'], 'send-default-help');
    select.value = String(!enabled);
    ui.responses.push({ body: { saved: true } }); await ui.submit('save');
    assert.equal(ui.requests[0].data.send_enabled, !enabled);
    assert.equal(select.value, String(!enabled));
    checked++;
  }
  // A broken initial read must disable both actions, including synthetic submit.
  for (const load of [{reject:true}, {invalidJson:true}, {ok:false}, {body:{error:'bad config'}}, {body:{...config,send_enabled:'true'}}]) {
    const ui = await setup(load);
    assert.ok(ui.buttons.every(b => b.disabled));
    await ui.submit('save'); await ui.submit('test');
    assert.equal(ui.requests.length, 0);
    assert.match(ui.result.textContent, /配置读取失败/);
    checked++;
  }
  // No submit before loading finishes. A refresh can then recover normally.
  {
    let finish;
    const pending = new Promise(resolve => { finish = resolve; });
    const ui = await setup({pending});
    await ui.submit('save'); assert.equal(ui.requests.length, 0);
    assert.ok(ui.buttons.every(b => b.disabled));
    finish(); await new Promise(resolve => setImmediate(resolve));
    assert.ok(ui.buttons.every(b => !b.disabled)); checked++;
  }
  // Failed saves preserve the selected default and permit a retry.
  for (const response of [{reject:true}, {invalidJson:true}, {ok:false,body:{error:'failed'}}, {body:{saved:false}}]) {
    const ui = await setup(); ui.form.elements.send_enabled.value = 'true';
    ui.responses.push(response); await ui.submit('save');
    assert.equal(ui.form.elements.send_enabled.value, 'true');
    assert.ok(ui.buttons.every(b => !b.disabled));
    ui.responses.push({body:{saved:true}}); await ui.submit('save');
    assert.equal(ui.requests[1].data.send_enabled, true); checked++;
  }
  // Block duplicate requests while pending; do not overwrite newer selection.
  {
    const ui = await setup(); let finish;
    const pending = new Promise(resolve => { finish = resolve; });
    ui.form.elements.send_enabled.value = 'true';
    ui.responses.push({pending,body:{saved:true}});
    const saving = ui.submit('save');
    assert.ok(ui.buttons.every(b => b.disabled));
    await ui.submit('save'); await ui.submit('test');
    assert.equal(ui.requests.length, 1);
    ui.form.elements.send_enabled.value = 'false'; finish(); await saving;
    assert.equal(ui.form.elements.send_enabled.value, 'false');
    assert.ok(ui.buttons.every(b => !b.disabled)); checked++;
  }
  process.stdout.write(JSON.stringify({ passed: checked }) + '\n');
}).on('error', err => { throw err; });

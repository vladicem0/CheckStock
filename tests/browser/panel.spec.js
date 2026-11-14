const {
  test, expect, P101, P102, P103, NORTH, SOUTH, OP, DATE,
  operation, historyEvent, deferred, openPanel, fillFbs,
} = require('./support');

test('matrix includes all six cells, zero quantities and archived stock', async ({page, dashboard}) => {
  dashboard.data.inventory.warehouses[1].archived = true;
  dashboard.data.inventory.warehouses[1].archived_at = DATE;
  for (const cell of dashboard.data.inventory.balances) {
    if (cell.warehouse_id === SOUTH) cell.archived = true;
  }
  await openPanel(page);
  await expect(page.locator('#stock-table tbody tr')).toHaveCount(3);
  await expect(page.locator('#stock-table .matrix-values')).toHaveCount(6);
  await expect(page.locator('#stock-table tbody tr').filter({hasText: 'SKU-103'}).locator('.matrix-values').first()).toHaveText('000');
  await expect(page.locator('#stock-table thead .badge.archive')).toHaveText('Архив');
  await expect(page.locator('#total-on-hand')).toHaveText('25');
  await expect(page.locator('#total-reserved')).toHaveText('0');
  await expect(page.locator('#total-available')).toHaveText('25');
  await expect(page.locator('#archive-note')).toBeVisible();
  await expect(page.locator(`#transfer-source option[value="${SOUTH}"]`)).toHaveText('Юг · архив');
  for (const selector of ['#receipt-warehouse', '#fbs-warehouse', '#fbo-warehouse', '#transfer-destination']) {
    await expect(page.locator(`${selector} option[value="${SOUTH}"]`)).toHaveCount(0);
  }
});

test('three transit lists display independent routes, statuses and totals', async ({page, dashboard}) => {
  const transfer = operation({kind: 'transfer', status: 'in_transit', qty: 3, destination_name: 'Юг', destination_warehouse_id: SOUTH});
  const fbo = operation({id: OP.replace('301', '302'), kind: 'fbo', status: 'in_transit', qty: 2, destination_name: 'Маркетплейс'});
  const fbs = operation({id: OP.replace('301', '303'), status: 'shipped', qty: 4});
  dashboard.data.inventory.transit = {ff: [transfer], fbo: [fbo], fbs: [fbs]};
  Object.assign(dashboard.data.inventory.totals, {on_hand: 16, available: 16, transit_ff: 3, transit_fbo: 2, transit_fbs: 4});
  await openPanel(page);
  for (const [category, qty, destination] of [['ff', 3, 'Юг'], ['fbo', 2, 'Маркетплейс'], ['fbs', 4, 'Клиент']]) {
    await expect(page.locator(`#total-transit-${category}`)).toHaveText(String(qty));
    await expect(page.locator(`#transit-${category}-list .transit-item`)).toHaveCount(1);
    await expect(page.locator(`#transit-${category}-list`)).toContainText(`Север → ${destination}`);
    await expect(page.locator(`#transit-${category}-list`)).toContainText('SKU-101');
  }
  await expect(page.locator('#transit-fbs-list .badge')).toHaveText('В пути к клиенту');
});

const commands = [
  {form: 'receipt', url: '/api/receipts', kind: 'receipt', status: 'completed', expected: {product_id: P101, warehouse_id: NORTH, qty: 3}},
  {form: 'transfer', url: '/api/transfers', kind: 'transfer', status: 'draft', expected: {product_id: P101, source_warehouse_id: NORTH, destination_warehouse_id: SOUTH, qty: 3}},
  {form: 'fbs', url: '/api/fbs/reservations', kind: 'fbs', status: 'reserved', expected: {product_id: P101, warehouse_id: NORTH, qty: 3, customer: 'Заказ 42'}},
  {form: 'fbo', url: '/api/fbo/shipments', kind: 'fbo', status: 'in_transit', expected: {product_id: P101, warehouse_id: NORTH, qty: 3, marketplace: 'Маркетплейс'}},
];
for (const command of commands) {
  test(`${command.form} form sends typed fields and a creation key`, async ({page, dashboard}) => {
    dashboard.handle = async ({route, path, method, body}) => {
      if (method !== 'POST' || path !== command.url) return false;
      await route.fulfill({json: operation({kind: command.kind, status: command.status, qty: body.qty, idempotency_key: body.idempotency_key})});
      return true;
    };
    await openPanel(page);
    await page.locator(`#${command.form}-qty`).fill('3');
    if (command.form === 'fbs') await page.locator('#fbs-customer').fill('  Заказ 42  ');
    if (command.form === 'fbo') await page.locator('#fbo-marketplace').fill('  Маркетплейс  ');
    await page.locator(`#${command.form}-form button[type="submit"]`).click();
    await expect(page.locator(`#${command.form}-form .form-status`)).toContainText('SKU-101');
    const writes = dashboard.requests.filter(request => request.method === 'POST');
    expect(writes).toHaveLength(1);
    expect(writes[0].body).toEqual({...command.expected, idempotency_key: expect.stringMatching(/^[0-9a-f-]{36}$/)});
  });
}

const transitions = [
  ['transfer', 'draft', 'Отправить', 'transfers', 'dispatch', 'in_transit', 'transfer_dispatched', 'В пути'],
  ['transfer', 'in_transit', 'Принять', 'transfers', 'receive', 'received', 'transfer_received', 'Принято'],
  ['fbs', 'reserved', 'Отправить', 'fbs', 'ship', 'shipped', 'fbs_shipped', 'В пути к клиенту'],
  ['fbs', 'reserved', 'Отменить', 'fbs', 'cancel', 'cancelled', 'fbs_cancelled', 'Отменено'],
  ['fbo', 'in_transit', 'Принять FBO', 'fbo', 'receive', 'received', 'fbo_received', 'Принято'],
];
for (const [kind, before, label, category, suffix, after, eventType, statusLabel] of transitions) {
  test(`${kind} ${suffix} uses the operation ID and refreshes status/history`, async ({page, dashboard}) => {
    dashboard.data.operations = [operation({kind, status: before})];
    dashboard.handle = async ({route, path, method}) => {
      if (method !== 'POST' || path !== `/api/${category}/${OP}/${suffix}`) return false;
      dashboard.data.operations[0].status = after;
      dashboard.data.history = [historyEvent({event_type: eventType, operation_status: after})];
      await route.fulfill({json: dashboard.data.operations[0]});
      return true;
    };
    await openPanel(page);
    const row = page.locator(`[data-operation-id="${OP}"]`);
    await row.getByRole('button', {name: new RegExp(`^${label} ·`)}).click();
    await expect(row.locator('.badge')).toHaveText(statusLabel);
    await expect(page.locator('#history-body tr')).toHaveCount(1);
    await expect(page.locator('#history-body .badge')).toHaveText(statusLabel);
    expect(dashboard.requests.filter(request => request.method === 'POST')).toEqual([
      {method: 'POST', path: `/api/${category}/${OP}/${suffix}`, body: null},
    ]);
    if (after !== 'in_transit') await expect(row.locator('button')).toHaveCount(0);
  });
}

test('local shortage shows available/requested counts and does not switch warehouse', async ({page, dashboard}) => {
  dashboard.handle = async ({route, method, path}) => {
    if (method !== 'POST' || path !== '/api/fbs/reservations') return false;
    await route.fulfill({status: 409, json: {detail: 'Недостаточно доступного товара на выбранном ФФ', available_here: 0, requested: 1}});
    return true;
  };
  await openPanel(page);
  await fillFbs(page, {product: P103, qty: 1});
  await page.locator('#fbs-form button[type="submit"]').click();
  await expect(page.locator('#fbs-form .form-error')).toContainText(/Доступно[^\d]*0/);
  await expect(page.locator('#fbs-form .form-error')).toContainText(/(?:запрошено|нужно|запрошено)[^\d]*1/i);
  await expect(page.locator('#fbs-warehouse')).toHaveValue(NORTH);
  await expect(page.locator('#total-on-hand')).toHaveText('25');
  await expect(page.locator('#fbs-qty')).toBeEnabled();
});

for (const failure of ['lost-response', 'http-503', 'malformed-success']) {
  test(`uncertain ${failure} survives reload and replays original request after archive`, async ({page, dashboard}) => {
    const submissions = [];
    dashboard.handle = async ({route, method, path, body}) => {
      if (method !== 'POST' || path !== '/api/fbs/reservations') return false;
      submissions.push(body);
      if (submissions.length === 1) {
        dashboard.data.operations = [operation({qty: body.qty, idempotency_key: body.idempotency_key})];
        if (failure === 'lost-response') await route.abort('failed');
        else if (failure === 'http-503') await route.fulfill({status: 503, json: {detail: 'База данных временно недоступна'}});
        else await route.fulfill({json: {detail: 'Incomplete success'}});
      } else {
        await route.fulfill({json: dashboard.data.operations[0]});
      }
      return true;
    };
    await openPanel(page);
    await fillFbs(page, {qty: 4, customer: 'Исходный заказ'});
    await page.locator('#fbs-form button[type="submit"]').click();
    await expect(page.locator('#fbs-form .form-error')).toBeVisible();
    await expect(page.locator('#fbs-qty')).toBeDisabled();
    dashboard.data.inventory.warehouses[0].archived = true;
    dashboard.data.inventory.warehouses[0].archived_at = DATE;
    await page.reload();
    await expect(page.locator('#updated-at')).toContainText('Обновлено');
    await expect(page.locator('#fbs-warehouse')).toHaveValue(NORTH);
    await expect(page.locator('#fbs-qty')).toHaveValue('4');
    await expect(page.locator('#fbs-customer')).toHaveValue('Исходный заказ');
    await expect(page.locator('#fbs-warehouse')).toBeDisabled();
    await page.locator('#fbs-form button[type="submit"]').click();
    await expect(page.locator('#fbs-form .form-status')).toContainText('SKU-101');
    expect(submissions).toHaveLength(2);
    expect(submissions[1]).toEqual(submissions[0]);
    await expect(page.locator('#fbs-customer')).toBeEnabled();
  });
}

test('UUID fallback preserves an uncertain request across reload and gives new operations fresh keys', async ({page, dashboard}) => {
  await page.addInitScript(() => {
    const randomBytes = crypto.getRandomValues.bind(crypto);
    Object.defineProperty(crypto, 'randomUUID', {configurable: true, value: undefined});
    Object.defineProperty(crypto, 'getRandomValues', {configurable: true, value: bytes => {
      window.randomByteRequests = (window.randomByteRequests || 0) + 1;
      return randomBytes(bytes);
    }});
  });
  const submissions = [];
  dashboard.handle = async ({route, method, path, body}) => {
    if (method !== 'POST' || path !== '/api/fbs/reservations') return false;
    submissions.push(body);
    if (submissions.length === 1) await route.abort('failed');
    else await route.fulfill({json: operation({qty: body.qty, idempotency_key: body.idempotency_key})});
    return true;
  };
  await openPanel(page);
  await fillFbs(page, {qty: 3, customer: 'Сохранённый заказ'});
  await page.locator('#fbs-form button[type="submit"]').click();
  await expect(page.locator('#fbs-form .form-error')).toBeVisible();
  expect(submissions).toHaveLength(1);
  expect(submissions[0].idempotency_key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  expect(await page.evaluate(() => window.randomByteRequests)).toBe(1);
  await page.reload();
  await expect(page.locator('#updated-at')).toContainText('Обновлено');
  await expect(page.locator('#fbs-qty')).toHaveValue('3');
  await expect(page.locator('#fbs-customer')).toHaveValue('Сохранённый заказ');
  await expect(page.locator('#fbs-qty')).toBeDisabled();
  await page.locator('#fbs-form button[type="submit"]').click();
  await expect(page.locator('#fbs-form .form-status')).toContainText('SKU-101');
  expect(submissions).toHaveLength(2);
  expect(submissions[1]).toEqual(submissions[0]);
  expect(await page.evaluate(() => window.randomByteRequests || 0)).toBe(0);

  await fillFbs(page, {qty: 1, customer: 'Новый заказ'});
  await page.locator('#fbs-form button[type="submit"]').click();
  await expect(page.locator('#fbs-qty')).toHaveValue('');
  expect(submissions).toHaveLength(3);
  expect(submissions[2].idempotency_key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  expect(submissions[2].idempotency_key).not.toBe(submissions[0].idempotency_key);
  expect(await page.evaluate(() => window.randomByteRequests)).toBe(1);
});

test('unavailable secure randomness reports an inline error without sending or locking the form', async ({page, dashboard}) => {
  await page.addInitScript(() => {
    Object.defineProperty(crypto, 'randomUUID', {configurable: true, value: undefined});
    Object.defineProperty(crypto, 'getRandomValues', {configurable: true, value: () => {
      throw new Error('Synthetic secure randomness failure');
    }});
  });
  await openPanel(page);
  await fillFbs(page);
  await page.locator('#fbs-form button[type="submit"]').click();
  await expect(page.locator('#fbs-form .form-error')).toHaveText('Не удалось создать ключ операции. Обновите страницу и повторите попытку.');
  expect(dashboard.requests.filter(request => request.method !== 'GET')).toHaveLength(0);
  for (const selector of ['#fbs-product', '#fbs-warehouse', '#fbs-qty', '#fbs-customer', '#fbs-form button[type="submit"]']) {
    await expect(page.locator(selector)).toBeEnabled();
  }
  await expect(page.locator('#fbs-qty')).toHaveValue('4');
  await page.locator('#fbs-qty').fill('2');
  await expect(page.locator('#fbs-qty')).toHaveValue('2');
});

test('successful mutation followed by failed refresh is not submitted twice', async ({page, dashboard}) => {
  let committed = false;
  let failReports = true;
  dashboard.handle = async ({route, method, path, body}) => {
    if (method === 'POST' && path === '/api/fbs/reservations') {
      committed = true;
      dashboard.data.operations = [operation({idempotency_key: body.idempotency_key})];
      await route.fulfill({json: dashboard.data.operations[0]});
      return true;
    }
    if (committed && failReports && method === 'GET' && path === '/api/stocks') {
      await route.fulfill({status: 503, json: {detail: 'Нет соединения'}});
      return true;
    }
    return false;
  };
  await openPanel(page);
  await fillFbs(page);
  await page.locator('#fbs-form button[type="submit"]').click();
  await expect(page.locator('#fbs-form .form-status')).toContainText('SKU-101');
  await expect(page.locator('#global-error')).toContainText('Операция выполнена');
  failReports = false;
  await page.locator('#refresh-button').click();
  await expect(page.locator(`[data-operation-id="${OP}"]`)).toBeVisible();
  await expect(page.locator('#global-error')).toBeHidden();
  expect(dashboard.requests.filter(request => request.method === 'POST')).toHaveLength(1);
});

test('pending operation remains disabled when a manual refresh replaces its row', async ({page, dashboard}) => {
  const started = deferred();
  const release = deferred();
  dashboard.data.operations = [operation()];
  dashboard.handle = async ({route, method, path}) => {
    if (method !== 'POST' || path !== `/api/fbs/${OP}/ship`) return false;
    started.resolve();
    await release.promise;
    dashboard.data.operations[0].status = 'shipped';
    await route.fulfill({json: dashboard.data.operations[0]});
    return true;
  };
  await openPanel(page);
  const row = page.locator(`[data-operation-id="${OP}"]`);
  await row.getByRole('button', {name: /^Отправить ·/}).click();
  await started.promise;
  await page.locator('#refresh-button').click();
  await expect(page.locator('#refresh-button')).toBeEnabled();
  await expect(row.getByRole('button', {name: /^Отправить ·/})).toBeDisabled();
  await expect(row.getByRole('button', {name: /^Отменить ·/})).toBeDisabled();
  release.resolve();
  await expect(row.locator('.badge')).toHaveText('В пути к клиенту');
  expect(dashboard.requests.filter(request => request.method === 'POST')).toHaveLength(1);
});

test('older failed refresh cannot replace a newer successful mutation refresh', async ({page, dashboard}) => {
  let holdNext = false;
  const started = deferred();
  const release = deferred();
  dashboard.handle = async ({route, method, path, body}) => {
    if (method === 'GET' && path === '/api/stocks' && holdNext) {
      holdNext = false;
      started.resolve();
      await release.promise;
      await route.fulfill({status: 503, json: {detail: 'Obsolete failed response'}});
      return true;
    }
    if (method === 'POST' && path === '/api/fbs/reservations') {
      dashboard.data.operations = [operation({idempotency_key: body.idempotency_key})];
      await route.fulfill({json: dashboard.data.operations[0]});
      return true;
    }
    return false;
  };
  await openPanel(page);
  await fillFbs(page);
  holdNext = true;
  await page.locator('#refresh-button').click();
  await started.promise;
  await page.locator('#fbs-form button[type="submit"]').click();
  await expect(page.locator(`[data-operation-id="${OP}"]`)).toBeVisible();
  const oldResponse = page.waitForResponse(response => response.url().endsWith('/api/stocks') && response.status() === 503);
  release.resolve();
  await oldResponse;
  await page.waitForLoadState('networkidle');
  await expect(page.locator('#global-error')).toBeHidden();
  await expect(page.locator('#updated-at')).toContainText('Обновлено');
});

test('refresh preserves an unsaved warehouse rename and history keeps its snapshot', async ({page, dashboard}) => {
  dashboard.data.history = [historyEvent()];
  dashboard.handle = async ({route, method, path, body}) => {
    if (method !== 'PATCH' || path !== `/api/warehouses/${NORTH}`) return false;
    dashboard.data.inventory.warehouses[0].name = body.name;
    await route.fulfill({json: dashboard.data.inventory.warehouses[0]});
    return true;
  };
  await openPanel(page);
  const input = page.locator(`#warehouse-rename-${NORTH}`);
  await input.fill('Новый Север');
  await page.locator('#refresh-button').click();
  await expect(page.locator('#refresh-button')).toBeEnabled();
  await expect(input).toHaveValue('Новый Север');
  await input.locator('..').getByRole('button', {name: 'Сохранить имя'}).click();
  await expect(page.locator('#stock-table thead')).toContainText('Новый Север');
  await expect(page.locator('#history-body')).toContainText('Север');
  await expect(page.locator('#history-body')).not.toContainText('Новый Север');
});

test('archive retains stock and history without silently choosing a new source', async ({page, dashboard}) => {
  dashboard.data.history = [historyEvent({from_name: 'Юг'})];
  dashboard.handle = async ({route, method, path}) => {
    if (method !== 'DELETE' || path !== `/api/warehouses/${SOUTH}`) return false;
    dashboard.data.inventory.warehouses[1].archived = true;
    dashboard.data.inventory.warehouses[1].archived_at = DATE;
    await route.fulfill({json: dashboard.data.inventory.warehouses[1]});
    return true;
  };
  await openPanel(page);
  await page.locator('#fbs-warehouse').selectOption(SOUTH);
  await page.getByRole('button', {name: 'Архивировать ФФ «Юг»', exact: true}).click();
  await page.getByRole('group', {name: 'Подтверждение архивации ФФ «Юг»'}).getByRole('button', {name: 'Подтвердить архивацию'}).click();
  await expect(page.locator('#archive-note')).toBeVisible();
  await expect(page.locator('#fbs-warehouse')).toHaveValue('');
  await expect(page.locator('#fbs-form button[type="submit"]')).toBeDisabled();
  await expect(page.locator('#total-on-hand')).toHaveText('25');
  await expect(page.locator('#stock-table .matrix-values')).toHaveCount(6);
  await expect(page.locator('#history-body')).toContainText('Юг');
  await expect(page.locator(`#transfer-source option[value="${SOUTH}"]`)).toHaveCount(1);
  await expect(page.locator(`#transfer-destination option[value="${SOUTH}"]`)).toHaveCount(0);
});

const warehouseWarning = 'Результат изменения склада не подтверждён. Нажмите «Обновить данные» и проверьте список складов, прежде чем повторять действие.';
for (const action of ['create', 'rename', 'archive']) {
  for (const uncertain of [true, false]) {
    test(`warehouse ${action} ${uncertain ? 'uncertain result advises verification' : 'definite rejection keeps only the domain error'}`, async ({page, dashboard}) => {
      const method = {create: 'POST', rename: 'PATCH', archive: 'DELETE'}[action];
      const path = action === 'create' ? '/api/warehouses' : `/api/warehouses/${NORTH}`;
      const createdId = '00000000-0000-4000-8000-000000000203';
      dashboard.handle = async ({route, method: requestMethod, path: requestPath, body}) => {
        if (requestMethod !== method || requestPath !== path) return false;
        if (uncertain) {
          // The command may have committed before its response was lost.
          if (action === 'create') {
            dashboard.data.inventory.warehouses.push({id: createdId, name: body.name, archived: false, archived_at: null, created_at: DATE});
            for (const product of dashboard.data.inventory.products) {
              dashboard.data.inventory.balances.push({
                product_id: product.id, warehouse_id: createdId, sku: product.sku,
                warehouse_name: body.name, archived: false, on_hand: 0, reserved_fbs: 0, available: 0,
              });
            }
          } else if (action === 'rename') {
            dashboard.data.inventory.warehouses[0].name = body.name;
          } else {
            dashboard.data.inventory.warehouses[0].archived = true;
            dashboard.data.inventory.warehouses[0].archived_at = DATE;
          }
          if (action === 'rename') await route.abort('failed');
          else await route.fulfill({status: 503, json: {detail: 'База данных временно недоступна'}});
        } else {
          await route.fulfill({status: 409, json: {detail: 'Изменение склада отклонено'}});
        }
        return true;
      };
      await openPanel(page);
      if (action === 'create') {
        await page.locator('#warehouse-name').fill('Восток');
        await page.locator('#warehouse-form button[type="submit"]').click();
      } else if (action === 'rename') {
        const input = page.locator(`#warehouse-rename-${NORTH}`);
        await input.fill('Новый Север');
        await input.locator('..').getByRole('button', {name: 'Сохранить имя'}).click();
      } else {
        await page.getByRole('button', {name: 'Архивировать ФФ «Север»', exact: true}).click();
        await page.getByRole('group', {name: 'Подтверждение архивации ФФ «Север»'}).getByRole('button', {name: 'Подтвердить архивацию'}).click();
      }
      const error = page.locator(action === 'create' ? '#warehouse-form .form-error' : '#warehouses-error');
      if (uncertain) await expect(error).toContainText(warehouseWarning);
      else await expect(error).toHaveText('Изменение склада отклонено');
      expect(dashboard.requests.filter(request => request.method !== 'GET')).toHaveLength(1);
      await page.locator('#refresh-button').click();
      await expect(page.locator('#refresh-button')).toBeEnabled();
      if (uncertain) {
        if (action === 'create') await expect(page.locator(`[data-warehouse-id="${createdId}"] h3`)).toHaveText('Восток');
        else if (action === 'rename') await expect(page.locator(`[data-warehouse-id="${NORTH}"] h3`)).toHaveText('Новый Север');
        else await expect(page.locator(`[data-warehouse-id="${NORTH}"] .badge.archive`)).toHaveText('Архив');
      }
      expect(dashboard.requests.filter(request => request.method !== 'GET')).toHaveLength(1);
    });
  }
}

test('hostile names remain text across matrix, operations, warehouses and history', async ({page, dashboard}) => {
  const hostile = '<img src=x onerror="window.injected=true">';
  dashboard.data.inventory.products[0].name = hostile;
  dashboard.data.inventory.warehouses[0].name = hostile;
  dashboard.data.operations = [operation({product_name: hostile, source_name: hostile, destination_name: hostile})];
  dashboard.data.history = [historyEvent({from_name: hostile, to_name: hostile})];
  await openPanel(page);
  for (const selector of ['#stock-table', '#operations-body', '#warehouse-list', '#history-body']) {
    await expect(page.locator(selector)).toContainText(hostile);
    await expect(page.locator(`${selector} img`)).toHaveCount(0);
  }
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
});

test('narrow viewport keeps overflow inside table containers and exposes all forms', async ({page, dashboard}) => {
  await page.setViewportSize({width: 390, height: 844});
  await openPanel(page);
  const dimensions = await page.evaluate(() => ({
    page: document.documentElement.scrollWidth, viewport: window.innerWidth,
    table: document.querySelector('#stock-table').scrollWidth,
    container: document.querySelector('#stock-table').parentElement.clientWidth,
  }));
  expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport + 1);
  expect(dimensions.table).toBeGreaterThan(dimensions.container);
  for (const id of ['receipt', 'transfer', 'fbs', 'fbo']) {
    await expect(page.locator(`#${id}-form`)).toBeVisible();
    await expect(page.locator(`#${id}-form button[type="submit"]`)).toBeEnabled();
  }
});

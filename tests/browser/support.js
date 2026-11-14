const {test: base, expect} = require('@playwright/test');

const P101 = '00000000-0000-4000-8000-000000000101';
const P102 = '00000000-0000-4000-8000-000000000102';
const P103 = '00000000-0000-4000-8000-000000000103';
const NORTH = '00000000-0000-4000-8000-000000000201';
const SOUTH = '00000000-0000-4000-8000-000000000202';
const OP = '00000000-0000-4000-8000-000000000301';
const DATE = '2026-11-15T00:00:00Z';

function initialData() {
  const products = [
    {id: P101, sku: 'SKU-101', name: 'Органайзер'},
    {id: P102, sku: 'SKU-102', name: 'Контейнер'},
    {id: P103, sku: 'SKU-103', name: 'Корзина'},
  ];
  const warehouses = [
    {id: NORTH, name: 'Север', archived: false, archived_at: null, created_at: DATE},
    {id: SOUTH, name: 'Юг', archived: false, archived_at: null, created_at: DATE},
  ];
  const amounts = [[10, 6], [5, 0], [0, 4]];
  const balances = products.flatMap((product, p) => warehouses.map((warehouse, w) => ({
    product_id: product.id, warehouse_id: warehouse.id, sku: product.sku,
    warehouse_name: warehouse.name, archived: false, on_hand: amounts[p][w],
    reserved_fbs: 0, available: amounts[p][w],
  })));
  return {
    inventory: {
      products, warehouses, balances,
      totals: {on_hand: 25, reserved_fbs: 0, available: 25, transit_ff: 0, transit_fbo: 0, transit_fbs: 0},
      transit: {ff: [], fbo: [], fbs: []},
    },
    operations: [], history: [],
  };
}

function operation(overrides = {}) {
  return {
    id: OP, kind: 'fbs', status: 'reserved', product_id: P101,
    sku: 'SKU-101', product_name: 'Органайзер', qty: 4,
    source_warehouse_id: NORTH, destination_warehouse_id: null,
    source_name: 'Север', destination_name: 'Клиент', external_party: 'Клиент',
    idempotency_key: 'fixture-operation', created_at: DATE, updated_at: DATE,
    ...overrides,
  };
}

function historyEvent(overrides = {}) {
  return {
    id: '00000000-0000-4000-8000-000000000401', operation_id: OP,
    event_type: 'fbs_reserved', product_id: P101, sku: 'SKU-101', qty: 4,
    from_warehouse_id: NORTH, to_warehouse_id: null,
    from_name: 'Север', to_name: 'Клиент', operation_status: 'reserved',
    created_at: DATE, ...overrides,
  };
}

function deferred() {
  let resolve;
  const promise = new Promise(done => {resolve = done;});
  return {promise, resolve};
}

// Browser behavior fixtures are deliberately not a replacement stock service.
// Each test supplies fixed API responses. Every API request is intercepted,
// including unexpected mutations, so no test can modify a running database.
const test = base.extend({
  dashboard: async ({page, context, baseURL}, use) => {
    const data = initialData();
    const requests = [];
    const unexpected = [];
    const scriptErrors = [];
    const mock = {data, requests, handle: null};
    page.on('pageerror', error => scriptErrors.push(error.message));
    const origin = new URL(baseURL).origin;
    await context.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      const method = request.method();
      if (url.origin !== origin) {
        unexpected.push(`External request: ${method} ${url.origin}${path}`);
        await route.abort();
        return;
      }
      if (!path.startsWith('/api/')) {
        if (method === 'GET' && (path === '/' || path.startsWith('/static/') || path === '/favicon.ico')) {
          await route.continue();
        } else {
          unexpected.push(`Unexpected non-API request: ${method} ${path}`);
          await route.abort();
        }
        return;
      }
      const body = request.postData() ? request.postDataJSON() : null;
      requests.push({method, path, body});
      if (mock.handle && await mock.handle({route, method, path, body})) return;
      const reports = {
        '/api/stocks': data.inventory, '/api/operations': data.operations,
        '/api/history': data.history, '/api/warehouses': data.inventory.warehouses,
      };
      if (method === 'GET' && Object.hasOwn(reports, path)) {
        await route.fulfill({json: reports[path]});
        return;
      }
      unexpected.push(`Unexpected intercepted API request: ${method} ${path}`);
      await route.fulfill({status: 500, json: {detail: 'Unexpected test request'}});
    });
    await use(mock);
    expect(unexpected).toEqual([]);
    expect(scriptErrors).toEqual([]);
  },
});

async function openPanel(page) {
  await page.goto('/');
  await expect(page.locator('#updated-at')).toContainText('Обновлено');
}

async function fillFbs(page, {product = P101, warehouse = NORTH, qty = 4, customer = 'Заказ 42'} = {}) {
  await page.locator('#fbs-product').selectOption(product);
  await page.locator('#fbs-warehouse').selectOption(warehouse);
  await page.locator('#fbs-qty').fill(String(qty));
  await page.locator('#fbs-customer').fill(customer);
}

module.exports = {test, expect, P101, P102, P103, NORTH, SOUTH, OP, DATE, operation, historyEvent, deferred, openPanel, fillFbs};

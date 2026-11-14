const {defineConfig} = require('@playwright/test');

const baseURL = process.env.CHECKSTOCK_BROWSER_URL || 'http://127.0.0.1:8000';
const target = new URL(baseURL);
if (target.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]'].includes(target.hostname)) {
  throw new Error('Browser checks require an explicit local HTTP application URL.');
}

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: '*.spec.js',
  forbidOnly: true,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 20000,
  expect: {timeout: 5000},
  reporter: 'list',
  outputDir: 'test-results',
  use: {
    baseURL,
    browserName: 'chromium',
    headless: true,
    viewport: {width: 1280, height: 900},
    locale: 'ru-RU',
    timezoneId: 'Asia/Omsk',
    serviceWorkers: 'block',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});

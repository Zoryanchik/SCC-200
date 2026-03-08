const { test, expect } = require('@playwright/test')

test('homepage visual snapshot', async ({ page }) => {
  await page.goto('http://localhost:5075')
  await page.waitForLoadState('networkidle')
  expect(await page.screenshot()).toMatchSnapshot('home.png')
})

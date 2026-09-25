import { randomUUID } from 'node:crypto'
import { test as base, expect, type BrowserContext } from '@playwright/test'
import { createDevSession } from '../scripts/dev-session.cjs'

// Sign a browser context in as a brand-new user.
export async function signInAsNewUser(context: BrowserContext, baseURL: string) {
  const session = await createDevSession({ email: `e2e-${randomUUID()}@rap-flow.local` })
  await context.addCookies([{ name: session.cookieName, value: session.cookieValue, url: baseURL }])
  return { email: session.email }
}

// `signedIn` gives each test its own fresh user, so parallel tests never see
// each other's jobs.
export const test = base.extend<{ signedIn: { email: string } }>({
  signedIn: async ({ context, baseURL }, use) => {
    await use(await signInAsNewUser(context, baseURL!))
  },
})

export { expect }

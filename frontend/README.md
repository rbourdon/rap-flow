This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Database

This project uses Prisma with a PostgreSQL (Neon) database. The schema lives in
`prisma/schema.prisma`.

Set `DATABASE_URL` to your Postgres connection string, then sync the schema so the
required tables (`user`, `session`, `account`, `verification`, `job`) exist. **If
the schema is not synced, `GET /` and other database-backed routes return a 500
error because the tables are missing.**

```bash
# Sync the Prisma schema to the database referenced by DATABASE_URL
npm run db:deploy
```

`db:deploy` runs `prisma db push`, which reconciles the database with
`schema.prisma` without requiring a migration history. This is intentional: the
database may already contain tables (e.g. created by Better Auth), so
`prisma migrate deploy` would fail with `P3005 "The database schema is not empty"`.
`db push` instead creates only the missing tables/columns and is safe to re-run on
every deploy.

The `build` script runs `db:deploy` automatically, so the schema is synced on
deploy whenever `DATABASE_URL` is available (it is skipped with a warning when
`DATABASE_URL` is not set). After editing `schema.prisma`, apply the changes with:

```bash
npx prisma db push
```

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.

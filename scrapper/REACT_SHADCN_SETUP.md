# React + shadcn Setup Guide for This Repo

This repository is currently a Python/FastAPI backend and does **not** include:
- `package.json`
- TypeScript config
- Tailwind config
- shadcn `components.json`

To use `components/ui/spooky-smoke-animation.tsx`, create a frontend app first.

## 1) Create a TypeScript + Tailwind app

From repo root:

```bash
npx create-next-app@latest frontend --typescript --tailwind --eslint --app --src-dir --import-alias "@/*"
cd frontend
```

## 2) Initialize shadcn

```bash
npx shadcn@latest init -d
```

## 3) Component and style paths

With the `--src-dir` setup above, defaults are:
- Components: `src/components/ui`
- Global styles: `src/app/globals.css`

If your project uses a different components path, create or keep a dedicated `ui` folder (for example `src/components/ui` or `/components/ui`) because shadcn-generated components assume a stable UI location and import alias pattern.

## 4) Copy component files

Copy these files into your frontend app:
- `components/ui/spooky-smoke-animation.tsx` -> `frontend/src/components/ui/spooky-smoke-animation.tsx`
- `components/ui/demo.tsx` -> `frontend/src/components/ui/demo.tsx` (or another demo/story path)

## 5) Dependencies for this component

No extra npm packages are required beyond React/Next defaults.
- No image assets required
- No icons required (`lucide-react` not needed for this component)

## 6) Use it in a page

Example in `frontend/src/app/page.tsx`:

```tsx
import { SmokeBackground } from "@/components/ui/spooky-smoke-animation";

export default function Page() {
  return (
    <main className="h-screen w-screen">
      <SmokeBackground smokeColor="#FF0000" />
    </main>
  );
}
```

## 7) Run frontend

```bash
cd frontend
npm run dev
```

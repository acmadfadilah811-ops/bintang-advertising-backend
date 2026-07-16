# Project Rules — Bintang ERP (bintang-advertising-backend + bintang-react-frontend)

Stack: **Backend** Django 6 / DRF / SimpleJWT / drf-spectacular. **Frontend**
React 19 / Vite 8 / React Router 7 / Axios.

The #1 maintainability problem in this codebase is **god files** — single files
that grew to thousands of lines. Your job when continuing development is to
**stop that growth and reverse it**, never to add to it.

---

## 0. The hard rule: no god files

- **Never add new code to an already-oversized file.** If the right place to put
  code is a file that is already too big, you must **split that file first**,
  then add your code to the correct new module.
- **File length limits** (lines of code, excluding blank/comments):
  - Python module: **soft 300 / hard 400**.
  - React component (`.jsx`): **soft 200 / hard 300**.
  - Any other module: soft 300 / hard 400.
- Before finishing a task, if any file you touched **exceeds the hard limit**,
  you MUST refactor it into smaller units as part of the same task — or, if that
  is out of scope, stop and explicitly flag it to the user instead of shipping a
  bigger god file.
- These files are known offenders. **When you touch them, extract — do not
  extend:**
  - Backend: `api/views.py` (~3,700 lines)
  - Frontend: `PointOfSale.jsx` (~3,400), `ProductDetailPage.jsx` (~2,800),
    `Orders.jsx` (~2,500), `StockInPage.jsx` (~2,300)
- **No duplicate/parallel implementations.** Do not create a second version of an
  existing feature. If a migration is genuinely half-done, finish it and delete
  the old one — never leave two live implementations of the *same* domain.
- **But verify "duplicate" before believing it.** Similar names are not evidence.
  These four modules look like two duplicated pairs and are **not** — they are
  four distinct domains, and deleting any of them destroys working features:
  - `Inventory` (`/inventory`) → **raw materials** (`InventoryItem`: tinta, roll).
    The *only* place raw materials can be managed/restocked.
  - `ProductInventoryApp` (`/product-inventory/*`) → **finished-product catalog**
    (`Product`/`ProductVariant`). Has no raw-material CRUD at all.
  - `CustomersPageLegacy` (`/customers`) → **CS & debt-collection dashboard**
    (`Contact`: order history, `POST /orders/:id/bayar/` settlement, CRM
    follow-up). The *only* place receivables are settled.
  - `CustomerSupplierApp` (`/customer-supplier/*`) → **master data**
    (`Customer`/`Supplier`: groups, discounts, deposits, reviews).
  Before calling anything a duplicate, check the **models and endpoints** behind
  it, not the file names.

---

## 1. Backend (Django / DRF) — how to split

Organize by **domain/feature**, following the apps that already exist
(`orders`, `inventory`, `pos`, `hr`, …). Do not funnel everything into the `api`
app.

- **Views:** convert `api/views.py` from one flat file into a `views/` package —
  one module per domain (`views/orders.py`, `views/inventory.py`,
  `views/pos.py`, …) with an `__init__.py` that re-exports. Prefer DRF
  `ViewSet`/`APIView` classes grouped by resource.
- **Serializers:** keep them in `serializers.py` (or a `serializers/` package),
  one per resource — never inline large serializers inside views.
- **Business logic → services.** Non-trivial logic (pricing, stock movements,
  POS transactions, payroll) goes in a `services/` module as plain functions,
  not inside views. Views stay thin: parse → call service → return.
- **One concern per file.** Models, serializers, views, permissions, services,
  and selectors/queries live in separate modules.
- **Permissions live in the backend.** Enforce role/ownership with real
  permission classes + `get_queryset` scoping — never rely on the frontend
  hiding a menu. (Hiding UI ≠ securing data.)

## 2. Frontend (React) — how to split

- **Components stay small and presentational.** A `.jsx` over ~200 lines must be
  broken into sub-components. Each component does one thing.
- **Extract logic into custom hooks.** Data fetching, form state, and business
  logic move into `useX()` hooks (e.g. `usePointOfSale`, `useStockIn`), out of
  the JSX. A component should read like a layout, not a program.
- **API calls go in a service/client module** (per domain, e.g.
  `services/orders.js`), never scattered `axios` calls inside components.
- **Feature-folder structure.** Group a feature's page, sub-components, hooks,
  and services under one folder (`features/pos/…`) instead of one huge file.
- When editing one of the big components above, pull at least the section you
  touch into its own sub-component/hook rather than editing in place.

## 3. Definition of done (every change)

1. No file you created or edited exceeds the hard line limit (or you flagged it).
2. Logic is in the right layer (service/hook), not dumped in a view/component.
3. No duplicate implementation of an existing feature was introduced.
4. You followed the existing folder/naming conventions of the repo.
5. Backend authorization is enforced server-side for any new/changed endpoint.

## 4. Working style

- Prefer small, reviewable diffs. Read the surrounding code before editing.
- Do not invent APIs, serializer fields, or endpoints — verify against the
  actual code (`drf-spectacular` schema is the source of truth for the API
  contract).
- If a task can't be done without growing a god file, say so and propose the
  split first, rather than silently making it worse.

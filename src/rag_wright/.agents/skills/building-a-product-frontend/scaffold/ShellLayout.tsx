import type { ReactNode } from 'react'
import { NavLink, Outlet } from 'react-router' // `react-router-dom` on an older router: change this line only

import { navigable, type Surface } from './surfaces'

/**
 * The application frame: `rail | main | detail`, with a fourth area reserved below.
 *
 *     rail  |  main    detail
 *     rail  |  run     run
 *
 * Core, not product: it is given its surfaces and its name and knows neither, which is what lets a second
 * application import this rather than copy it. The colour names are shadcn token names; the tokens are the
 * product's, the structure is the point.
 *
 * **The run area is RESERVED, not built.** Nothing renders there until an agent run exists. Reserving costs
 * one grid line; it buys that no surface is written assuming nothing can ever dock below it.
 */
export default function ShellLayout({ surfaces, productName, footer, detail }: {
  surfaces: Surface[]
  /** Supplied by the caller: the core must not know whose application it is drawing. */
  productName: string
  /** The foot of the rail: which account and workspace you are in is not a detail to drop. */
  footer?: ReactNode
  /**
   * The panel for the currently selected object. **Absent until something fills it.** An empty landmark is
   * worse than no landmark: a screen-reader user would find "Details", jump to it and arrive nowhere.
   */
  detail?: ReactNode
}) {
  return (
    <div className={[
      'min-h-screen lg:grid lg:grid-rows-[1fr_auto]',
      // **The third column exists only when `detail` does, and that is not tidiness.** Row 2 is reserved for
      // the run, so with two columns declared the detail panel -- the third child -- auto-places into the RUN
      // slot: underneath `main`, full width, off-screen. jsdom computes no grid, so assert in the BROWSER that
      // the panel is beside `main`.
      detail === undefined
        ? 'lg:grid-cols-[16rem_minmax(0,1fr)]'
        // Capped, so `main` takes every pixel a wider viewport adds. An uncapped panel ends up wider than the
        // content beside it (measured: 608px against 576px at 1440px).
        : 'lg:grid-cols-[16rem_minmax(0,1fr)_minmax(20rem,26rem)]',
    ].join(' ')}>
      {/* The first tab stop, so a keyboard user is not forced through the whole navigation on every surface. */}
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-primary focus:px-3 focus:py-2 focus:text-sm focus:text-primary-foreground">
        Skip to content
      </a>

      {/* **The RAIL.** Persistent, full height beside the content rather than a bar above it: it carries
          workspace switching and the library, which GROW, and a horizontal bar cannot grow. `complementary`
          rather than `navigation` because it holds more than links. */}
      <aside aria-label="Workspace" className="flex flex-col gap-4 border-b border-border p-4 lg:row-span-2 lg:h-screen lg:overflow-y-auto lg:border-b-0 lg:border-r">
        <span className="text-sm font-semibold tracking-tight">{productName}</span>

        {/* Named, so it is a landmark a screen reader can jump to rather than an anonymous group. */}
        <nav aria-label="Surfaces" className="flex flex-wrap gap-1 lg:flex-col lg:flex-nowrap">
          {navigable(surfaces).map((surface) => (
            <NavLink key={surface.path} to={surface.path} end={surface.nav?.end} className={navLinkClass}>
              {surface.nav?.label}
            </NavLink>
          ))}
        </nav>

        {/* `mt-auto`: at the foot of a tall rail, simply after the navigation on a short one. */}
        {footer === undefined ? null : <div className="lg:mt-auto">{footer}</div>}
      </aside>

      {/* Its own scroll container, so a long surface scrolls under a rail that stays put. */}
      <main id="main" className="min-w-0 lg:h-screen lg:overflow-y-auto">
        <Outlet />
      </main>

      {detail === undefined ? null : (
        <aside aria-label="Details" className="min-w-0 border-t border-border p-4 lg:h-screen lg:overflow-y-auto lg:border-l lg:border-t-0">
          {detail}
        </aside>
      )}
    </div>
  )
}

function navLinkClass({ isActive }: { isActive: boolean }): string {
  // **The current surface is marked by `aria-current="page"`, which NavLink sets itself; this class is the
  // visual echo.** The marker is a BORDER, not only a colour swap, so it stays legible in greyscale -- the half
  // of a contrast requirement a palette cannot satisfy. `min-h-11` is 44px, the minimum hit area.
  const base = 'inline-flex min-h-11 items-center rounded-md border-l-2 px-3 text-sm font-medium transition-colors'
  return isActive
    ? `${base} border-foreground bg-accent text-foreground`
    : `${base} border-transparent text-muted-foreground hover:bg-accent/50 hover:text-foreground`
}

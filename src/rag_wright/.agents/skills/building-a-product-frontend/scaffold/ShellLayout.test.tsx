/**
 * The shell's decisions, each with its guard (jsdom tier). What jsdom cannot see -- that the detail panel sits
 * BESIDE `main` rather than under it, and the 44px hit areas -- is asserted in the browser tier.
 */
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it } from 'vitest'

import ShellLayout from './ShellLayout'
import type { Surface } from './surfaces'

// Explicit, so the file does not depend on the runner's `globals` setting for testing-library's auto-cleanup.
afterEach(cleanup)

const SURFACES: Surface[] = [
  { path: '/alpha', element: <div>alpha surface</div>, nav: { label: 'Alpha', end: true } },
  { path: '/alpha/saved', element: <div>saved surface</div>, nav: { label: 'Saved' } },
  { path: '/beta', element: <div>beta surface</div>, nav: { label: 'Beta' } },
  // No `nav`: routed, but reached from somewhere other than the navigation.
  { path: '/hidden', element: <div>hidden surface</div> },
]

function renderShell(path: string, detail?: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<ShellLayout surfaces={SURFACES} productName="Testproduct" detail={detail} />}>
          {SURFACES.map((surface) => (
            <Route key={surface.path} path={surface.path} element={surface.element} />
          ))}
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

const current = (name: string) => screen.getByRole('link', { name }).getAttribute('aria-current')

describe('navigation', () => {
  it('renders a link for every surface that declares navigation, and none for one that does not', () => {
    renderShell('/hidden')
    const nav = screen.getByRole('navigation', { name: 'Surfaces' })
    expect(nav.textContent).toContain('Alpha')
    expect(nav.textContent).not.toContain('hidden')
    expect(screen.getByText('hidden surface')).toBeTruthy()
  })

  it('marks the current surface with aria-current, not with colour alone', () => {
    renderShell('/beta')
    expect(current('Beta')).toBe('page')
    expect(current('Alpha')).toBeNull()
  })

  it('does not leave a parent lit while its child is open (`end`)', () => {
    renderShell('/alpha/saved')
    expect(current('Saved')).toBe('page')
    expect(current('Alpha')).toBeNull()
  })
})

describe('the regions', () => {
  it('puts navigation in the RAIL, beside the content rather than in a bar above it', () => {
    renderShell('/alpha')
    const rail = screen.getByRole('complementary', { name: 'Workspace' })
    expect(rail.contains(screen.getByRole('navigation', { name: 'Surfaces' }))).toBe(true)
  })

  it('renders exactly one main landmark', () => {
    renderShell('/alpha')
    expect(screen.getAllByRole('main')).toHaveLength(1)
  })

  it('renders NO detail region until something fills it', () => {
    renderShell('/alpha')
    expect(screen.queryByRole('complementary', { name: 'Details' })).toBeNull()
  })

  it('renders the detail region a surface provides', () => {
    renderShell('/alpha', <p>the selected object</p>)
    expect(screen.getByRole('complementary', { name: 'Details' }).textContent).toContain('the selected object')
  })
})

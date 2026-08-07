import {
  Badge,
  Button,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  useQuery,
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

function JsonBlock({ value }) {
  return jsx('pre', {
    className: 'max-h-72 overflow-auto whitespace-pre-wrap text-xs text-(--ui-text-tertiary)',
    children: JSON.stringify(value, null, 2),
  })
}

function RouterPage({ ctx }) {
  const status = useQuery({
    queryKey: ['clickup-work-router', 'status'],
    queryFn: () => ctx.rest('/status'),
    refetchInterval: 15_000,
  })

  if (status.isLoading) {
    return jsx('div', { className: 'p-4 text-sm text-(--ui-text-tertiary)', children: 'Loading ClickUp Router status…' })
  }

  if (status.isError) {
    return jsxs('div', {
      className: 'flex h-full flex-col items-start gap-3 p-4 text-sm',
      children: [
        jsx('div', { className: 'font-medium', children: 'ClickUp Router backend unavailable' }),
        jsx('div', { className: 'text-(--ui-text-tertiary)', children: String(status.error) }),
        jsx(Button, { onClick: () => status.refetch(), children: 'Retry' }),
      ],
    })
  }

  const data = status.data || {}
  return jsxs('div', {
    className: 'flex h-full flex-col gap-4 overflow-auto p-4 text-sm',
    children: [
      jsxs('div', {
        className: 'flex items-start justify-between gap-3',
        children: [
          jsxs('div', {
            children: [
              jsx('div', { className: 'text-base font-medium', children: 'ClickUp Work Router' }),
              jsx('div', {
                className: 'text-(--ui-text-tertiary)',
                children: 'ClickUp stays authoritative for customer work; selected execution routes into Hermes Kanban.',
              }),
            ],
          }),
          jsx(Badge, { children: data.service?.ok ? 'healthy' : 'unknown' }),
        ],
      }),
      jsxs('section', {
        className: 'rounded-md border border-(--ui-stroke-secondary) p-3',
        children: [
          jsx('div', { className: 'mb-2 font-medium', children: 'Workspaces' }),
          jsx(JsonBlock, { value: data.workspaces || {} }),
        ],
      }),
      jsxs('section', {
        className: 'rounded-md border border-(--ui-stroke-secondary) p-3',
        children: [
          jsx('div', { className: 'mb-2 font-medium', children: 'Recent routing events' }),
          jsx(JsonBlock, { value: data.recent_events || [] }),
        ],
      }),
    ],
  })
}

export default {
  id: 'clickup-work-router',
  name: 'ClickUp Work Router',
  defaultEnabled: false,
  register(ctx) {
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/clickup-work-router' },
        render: () => jsx(RouterPage, { ctx }),
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        data: { path: '/clickup-work-router', label: 'ClickUp Router', codicon: 'project' },
      },
    ])
  },
}

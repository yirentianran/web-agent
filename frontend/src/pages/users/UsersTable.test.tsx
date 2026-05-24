import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import UsersTable from './UsersTable'
import type { UserItem } from '../../hooks/useUsersApi'

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      const map: Record<string, string> = {
        'users.userList': 'User List',
        'users.colUserId': 'User ID',
        'users.colRole': 'Role',
        'users.colStatus': 'Status',
        'users.colTokens': 'Tokens',
        'users.colSessions': 'Sessions',
        'users.colRegistered': 'Registered',
        'users.colLastActive': 'Last Active',
        'users.colActions': 'Actions',
        'users.roleAdmin': 'Admin',
        'users.roleUser': 'User',
        'users.statusActive': 'Active',
        'users.statusDisabled': 'Disabled',
        'users.currentUser': 'Current User',
        'users.actionPromote': 'Promote',
        'users.actionDemote': 'Demote',
        'users.actionDisable': 'Disable',
        'users.actionEnable': 'Enable',
        'users.empty': 'No users found',
        'users.disabledBy': `Disabled by ${opts?.admin} on ${opts?.date}`,
      }
      return map[key] ?? key
    },
    i18n: { changeLanguage: vi.fn() },
  }),
}))

const mockUsers: UserItem[] = [
  {
    user_id: 'admin',
    role: 'admin',
    status: 'active',
    created_at: 1713168000,
    last_active_at: 1715432150,
    disabled_at: null,
    disabled_by: null,
    session_count: 143,
    total_tokens: 2460000,
  },
  {
    user_id: 'zhangsan',
    role: 'user',
    status: 'active',
    created_at: 1713500000,
    last_active_at: 1715400000,
    disabled_at: null,
    disabled_by: null,
    session_count: 67,
    total_tokens: 856100,
  },
  {
    user_id: 'wangwu',
    role: 'user',
    status: 'disabled',
    created_at: 1713600000,
    last_active_at: 1715000000,
    disabled_at: 1715000000,
    disabled_by: 'admin',
    session_count: 3,
    total_tokens: 12400,
  },
]

describe('UsersTable', () => {
  const noop = vi.fn()

  it('renders column headers', () => {
    render(
      <UsersTable
        items={mockUsers}
        currentUserId="admin"
        loading={false}
        onDisable={noop}
        onEnable={noop}
        onPromote={noop}
        onDemote={noop}
      />,
    )
    expect(screen.getByText('User ID')).toBeTruthy()
    expect(screen.getByText('Role')).toBeTruthy()
    expect(screen.getByText('Status')).toBeTruthy()
    expect(screen.getByText('Actions')).toBeTruthy()
  })

  it('renders all user rows', () => {
    render(
      <UsersTable
        items={mockUsers}
        currentUserId="admin"
        loading={false}
        onDisable={noop}
        onEnable={noop}
        onPromote={noop}
        onDemote={noop}
      />,
    )
    expect(screen.getByText('admin')).toBeTruthy()
    expect(screen.getByText('zhangsan')).toBeTruthy()
    expect(screen.getByText('wangwu')).toBeTruthy()
  })

  it('shows current user label for the current user', () => {
    render(
      <UsersTable
        items={mockUsers}
        currentUserId="admin"
        loading={false}
        onDisable={noop}
        onEnable={noop}
        onPromote={noop}
        onDemote={noop}
      />,
    )
    expect(screen.getByText('Current User')).toBeTruthy()
  })

  it('shows enable button for disabled users', () => {
    render(
      <UsersTable
        items={mockUsers}
        currentUserId="admin"
        loading={false}
        onDisable={noop}
        onEnable={noop}
        onPromote={noop}
        onDemote={noop}
      />,
    )
    expect(screen.getByText('Enable')).toBeTruthy()
  })

  it('shows empty state when no items', () => {
    render(
      <UsersTable
        items={[]}
        currentUserId="admin"
        loading={false}
        onDisable={noop}
        onEnable={noop}
        onPromote={noop}
        onDemote={noop}
      />,
    )
    expect(screen.getByText('No users found')).toBeTruthy()
  })

  it('shows promote/disable buttons for active non-current users', () => {
    render(
      <UsersTable
        items={mockUsers}
        currentUserId="admin"
        loading={false}
        onDisable={noop}
        onEnable={noop}
        onPromote={noop}
        onDemote={noop}
      />,
    )
    expect(screen.getByText('Promote')).toBeTruthy()
    expect(screen.getByText('Disable')).toBeTruthy()
  })
})

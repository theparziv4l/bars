import { Outlet, Link, useLocation } from 'react-router-dom'
import {
    LayoutDashboard, FolderKanban, Search, FileText,
    Settings, Menu, X, Wifi, WifiOff, Bell, Library, LogOut, GitMerge, Users
} from 'lucide-react'
import { useAppStore, useAuthStore } from '../../stores'
import { useWebSocket } from '../../hooks/useWebSocket'

const navItems = [
    { path: '/', icon: LayoutDashboard, label: 'Dashboard' },
    { path: '/library', icon: Library, label: 'Library' },
    { path: '/search', icon: Search, label: 'Search' },
    { path: '/reports', icon: FileText, label: 'Reports' },
    { path: '/sync-peers', icon: GitMerge, label: 'Sync' },
    { path: '/users', icon: Users, label: 'Users', adminOnly: true },
]

export default function Layout() {
    const location = useLocation()
    const { sidebarOpen, toggleSidebar, wsConnected, notifications } = useAppStore()
    const { user, logout } = useAuthStore()

    // Initialize WebSocket connection
    useWebSocket()

    return (
        <div className="min-h-screen bg-dark-950 flex">
            {/* Sidebar */}
            <aside className={`
        fixed inset-y-0 left-0 z-50 w-64 bg-dark-900 border-r border-dark-800
        transform transition-transform duration-300 ease-in-out
        ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
        lg:translate-x-0 lg:static
      `}>
                {/* Logo */}
                <div className="h-16 flex items-center justify-between px-4 border-b border-dark-800">
                    <Link to="/" className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-accent-primary to-accent-secondary 
                          flex items-center justify-center shadow-lg glow-primary">
                            <span className="text-white font-bold text-lg">PT</span>
                        </div>
                        <div>
                            <h1 className="font-bold text-dark-50">Bars</h1>
                            <p className="text-xs text-dark-500">Orchestration Framework</p>
                        </div>
                    </Link>
                    <button onClick={toggleSidebar} className="lg:hidden text-dark-400 hover:text-dark-200">
                        <X size={20} />
                    </button>
                </div>

                {/* Navigation */}
                <nav className="p-4 space-y-1">
                    {navItems
                        .filter((item) => !item.adminOnly || user?.role === 'admin')
                        .map((item) => {
                            const isActive = location.pathname === item.path
                            return (
                                <Link
                                    key={item.path}
                                    to={item.path}
                                    className={`
                      flex items-center gap-3 px-4 py-3 rounded-lg transition-all duration-200
                      ${isActive
                                            ? 'bg-accent-primary/20 text-accent-primary border-l-2 border-accent-primary'
                                            : 'text-dark-400 hover:text-dark-200 hover:bg-dark-800'}
                    `}
                                >
                                    <item.icon size={20} />
                                    <span className="font-medium">{item.label}</span>
                                </Link>
                            )
                        })}
                </nav>

                {/* AGPL-3.0 §13: users interacting over a network must be offered the source. */}
                <a
                    href="https://github.com/saidtunc/bars"
                    target="_blank"
                    rel="noreferrer"
                    className="block px-4 py-3 text-xs text-dark-500 hover:text-dark-300"
                >
                    Bars — AGPL-3.0 · Source
                </a>

                {/* Connection Status */}
                <div className="absolute bottom-4 left-4 right-4">
                    <div className={`
            flex items-center gap-2 px-4 py-2 rounded-lg text-sm
            ${wsConnected ? 'bg-accent-success/10 text-accent-success' : 'bg-accent-danger/10 text-accent-danger'}
          `}>
                        {wsConnected ? <Wifi size={16} /> : <WifiOff size={16} />}
                        <span>{wsConnected ? 'Connected' : 'Disconnected'}</span>
                    </div>
                </div>
            </aside>

            {/* Main Content */}
            <div className="flex-1 flex flex-col min-h-screen">
                {/* Header */}
                <header className="h-16 bg-dark-900/80 backdrop-blur-md border-b border-dark-800 
                         flex items-center justify-between px-4 sticky top-0 z-40">
                    <div className="flex items-center gap-4">
                        <button
                            onClick={toggleSidebar}
                            className="lg:hidden p-2 text-dark-400 hover:text-dark-200 hover:bg-dark-800 rounded-lg"
                        >
                            <Menu size={20} />
                        </button>
                        <div className="hidden sm:block">
                            <h2 className="text-lg font-semibold text-dark-100">
                                {(() => {
                                    const path = location.pathname
                                    if (path === '/') return 'Dashboard'
                                    if (path.startsWith('/library')) return 'Library'
                                    if (path.startsWith('/reports')) return 'Reports'
                                    if (path.startsWith('/search')) return 'Search'
                                    if (path === '/sync-peers') return 'Sync'
                                    if (path === '/users') return 'Users'
                                    return 'Project'
                                })()}
                            </h2>
                        </div>
                    </div>

                    <div className="flex items-center gap-4">
                        {user && (
                            <div className="hidden md:flex items-center gap-2 text-sm text-dark-300">
                                <span className="px-2 py-1 rounded bg-dark-800 border border-dark-700 flex items-center gap-1.5">
                                    {user.username}
                                    <span className={`text-[10px] px-1 py-0.5 rounded font-medium ${
                                        user.role === 'admin'
                                            ? 'bg-amber-500/20 text-amber-400'
                                            : 'bg-dark-600 text-dark-400'
                                    }`}>{user.role}</span>
                                </span>
                                <button
                                    onClick={logout}
                                    className="btn-ghost btn-sm"
                                    title="Logout"
                                >
                                    <LogOut size={16} />
                                </button>
                            </div>
                        )}
                        {/* Notifications */}
                        <button className="relative p-2 text-dark-400 hover:text-dark-200 hover:bg-dark-800 rounded-lg">
                            <Bell size={20} />
                            {notifications.length > 0 && (
                                <span className="absolute top-1 right-1 w-2 h-2 bg-accent-danger rounded-full"></span>
                            )}
                        </button>

                        {/* Settings */}
                        <button className="p-2 text-dark-400 hover:text-dark-200 hover:bg-dark-800 rounded-lg">
                            <Settings size={20} />
                        </button>
                    </div>
                </header>

                {/* Page Content */}
                <main className="flex-1 p-6 overflow-auto">
                    <Outlet />
                </main>
            </div>

            {/* Mobile Overlay */}
            {sidebarOpen && (
                <div
                    className="fixed inset-0 bg-black/50 z-40 lg:hidden"
                    onClick={toggleSidebar}
                />
            )}
        </div>
    )
}

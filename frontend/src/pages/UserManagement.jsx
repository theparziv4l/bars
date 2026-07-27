import { useState, useEffect, useCallback } from 'react'
import {
    Users, Plus, Shield, User, ToggleLeft, ToggleRight,
    KeyRound, X, RefreshCw, Ban,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { usersApi } from '../services/api'
import { useAuthStore } from '../stores'

function CreateUserModal({ onClose, onCreated }) {
    const [form, setForm] = useState({ username: '', password: '', role: 'operator' })
    const [loading, setLoading] = useState(false)

    const onSubmit = async (e) => {
        e.preventDefault()
        setLoading(true)
        try {
            await usersApi.create(form)
            toast.success(`User '${form.username}' created`)
            onCreated()
            onClose()
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to create user')
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
            <div className="card w-full max-w-md">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="text-lg font-semibold text-dark-100">Create User</h3>
                    <button onClick={onClose} className="text-dark-400 hover:text-dark-200">
                        <X size={20} />
                    </button>
                </div>
                <form onSubmit={onSubmit} className="space-y-3">
                    <div>
                        <label className="block text-sm text-dark-300 mb-1">Username</label>
                        <input
                            className="input"
                            value={form.username}
                            onChange={(e) => setForm({ ...form, username: e.target.value })}
                            required
                            minLength={3}
                            autoFocus
                        />
                    </div>
                    <div>
                        <label className="block text-sm text-dark-300 mb-1">Password</label>
                        <input
                            type="password"
                            className="input"
                            value={form.password}
                            onChange={(e) => setForm({ ...form, password: e.target.value })}
                            required
                        />
                        <p className="text-xs text-dark-500 mt-1">User will be required to change this on first login</p>
                    </div>
                    <div>
                        <label className="block text-sm text-dark-300 mb-1">Role</label>
                        <select
                            className="input"
                            value={form.role}
                            onChange={(e) => setForm({ ...form, role: e.target.value })}
                        >
                            <option value="operator">Operator</option>
                            <option value="admin">Admin</option>
                        </select>
                    </div>
                    <button
                        type="submit"
                        disabled={loading}
                        className="btn-primary w-full disabled:opacity-60"
                    >
                        {loading ? 'Creating...' : 'Create User'}
                    </button>
                </form>
            </div>
        </div>
    )
}

function ResetPasswordModal({ targetUser, onClose, onReset }) {
    const [newPassword, setNewPassword] = useState('')
    const [loading, setLoading] = useState(false)

    const onSubmit = async (e) => {
        e.preventDefault()
        if (newPassword.length < 8) {
            toast.error('Password must be at least 8 characters')
            return
        }
        setLoading(true)
        try {
            await usersApi.resetPassword(targetUser.id, { new_password: newPassword })
            toast.success(`Password reset for '${targetUser.username}'`)
            onReset()
            onClose()
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to reset password')
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
            <div className="card w-full max-w-md">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="text-lg font-semibold text-dark-100">
                        Reset Password: {targetUser.username}
                    </h3>
                    <button onClick={onClose} className="text-dark-400 hover:text-dark-200">
                        <X size={20} />
                    </button>
                </div>
                <form onSubmit={onSubmit} className="space-y-3">
                    <div>
                        <label className="block text-sm text-dark-300 mb-1">New Password</label>
                        <input
                            type="password"
                            className="input"
                            value={newPassword}
                            onChange={(e) => setNewPassword(e.target.value)}
                            required
                            minLength={8}
                            autoFocus
                        />
                        <p className="text-xs text-dark-500 mt-1">User will be required to change this on next login</p>
                    </div>
                    <button
                        type="submit"
                        disabled={loading}
                        className="btn-primary w-full disabled:opacity-60"
                    >
                        {loading ? 'Resetting...' : 'Reset Password'}
                    </button>
                </form>
            </div>
        </div>
    )
}

export default function UserManagement() {
    const { user: currentUser } = useAuthStore()
    const [users, setUsers] = useState([])
    const [loading, setLoading] = useState(true)
    const [showCreate, setShowCreate] = useState(false)
    const [resetTarget, setResetTarget] = useState(null)

    const fetchUsers = useCallback(async () => {
        try {
            const { data } = await usersApi.list()
            setUsers(data)
        } catch (error) {
            toast.error('Failed to load users')
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        fetchUsers()
    }, [fetchUsers])

    const toggleActive = async (u) => {
        if (u.id === currentUser?.id) {
            toast.error('Cannot deactivate yourself')
            return
        }
        try {
            await usersApi.update(u.id, { is_active: !u.is_active })
            toast.success(`${u.username} ${u.is_active ? 'deactivated' : 'activated'}`)
            fetchUsers()
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to update user')
        }
    }

    const toggleRole = async (u) => {
        if (u.id === currentUser?.id) {
            toast.error('Cannot change your own role')
            return
        }
        const newRole = u.role === 'admin' ? 'operator' : 'admin'
        try {
            await usersApi.update(u.id, { role: newRole })
            toast.success(`${u.username} role changed to ${newRole}`)
            fetchUsers()
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to update role')
        }
    }

    const deleteUser = async (u) => {
        if (u.id === currentUser?.id) {
            toast.error('Cannot deactivate yourself')
            return
        }
        if (!window.confirm(`Deactivate user '${u.username}'?`)) return
        try {
            await usersApi.delete(u.id)
            toast.success(`${u.username} deactivated`)
            fetchUsers()
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to deactivate user')
        }
    }

    const formatDate = (d) => {
        if (!d) return '-'
        return new Date(d).toLocaleString()
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <Users size={24} className="text-accent-primary" />
                    <h1 className="text-2xl font-bold text-dark-50">User Management</h1>
                    <span className="text-sm text-dark-400">{users.length} users</span>
                </div>
                <div className="flex gap-2">
                    <button onClick={fetchUsers} className="btn-ghost btn-sm" title="Refresh">
                        <RefreshCw size={16} />
                    </button>
                    <button onClick={() => setShowCreate(true)} className="btn-primary btn-sm">
                        <Plus size={16} />
                        Create User
                    </button>
                </div>
            </div>

            {loading ? (
                <div className="flex items-center justify-center h-40">
                    <div className="w-8 h-8 border-2 border-accent-primary border-t-transparent rounded-full animate-spin" />
                </div>
            ) : (
                <div className="card overflow-hidden">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-dark-700 text-dark-400 text-left">
                                <th className="px-4 py-3 font-medium">Username</th>
                                <th className="px-4 py-3 font-medium">Role</th>
                                <th className="px-4 py-3 font-medium">Status</th>
                                <th className="px-4 py-3 font-medium hidden md:table-cell">Last Login</th>
                                <th className="px-4 py-3 font-medium hidden lg:table-cell">Created</th>
                                <th className="px-4 py-3 font-medium text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-dark-800">
                            {users.map((u) => (
                                <tr key={u.id} className={`hover:bg-dark-800/50 ${!u.is_active ? 'opacity-50' : ''}`}>
                                    <td className="px-4 py-3">
                                        <div className="flex items-center gap-2">
                                            {u.role === 'admin' ? (
                                                <Shield size={14} className="text-amber-400" />
                                            ) : (
                                                <User size={14} className="text-dark-400" />
                                            )}
                                            <span className="text-dark-100 font-medium">{u.username}</span>
                                            {u.id === currentUser?.id && (
                                                <span className="text-xs px-1.5 py-0.5 rounded bg-accent-primary/20 text-accent-primary">you</span>
                                            )}
                                            {u.must_change_password && (
                                                <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400">pw change</span>
                                            )}
                                        </div>
                                    </td>
                                    <td className="px-4 py-3">
                                        <span className={`text-xs px-2 py-1 rounded font-medium ${
                                            u.role === 'admin'
                                                ? 'bg-amber-500/20 text-amber-400'
                                                : 'bg-dark-700 text-dark-300'
                                        }`}>
                                            {u.role}
                                        </span>
                                    </td>
                                    <td className="px-4 py-3">
                                        <span className={`text-xs px-2 py-1 rounded font-medium ${
                                            u.is_active
                                                ? 'bg-accent-success/20 text-accent-success'
                                                : 'bg-accent-danger/20 text-accent-danger'
                                        }`}>
                                            {u.is_active ? 'active' : 'inactive'}
                                        </span>
                                    </td>
                                    <td className="px-4 py-3 text-dark-400 hidden md:table-cell">
                                        {formatDate(u.last_login_at)}
                                    </td>
                                    <td className="px-4 py-3 text-dark-400 hidden lg:table-cell">
                                        {formatDate(u.created_at)}
                                    </td>
                                    <td className="px-4 py-3">
                                        <div className="flex items-center gap-1 justify-end">
                                            {u.id !== currentUser?.id && (
                                                <>
                                                    <button
                                                        onClick={() => toggleRole(u)}
                                                        className="btn-ghost btn-sm"
                                                        title={`Change role to ${u.role === 'admin' ? 'operator' : 'admin'}`}
                                                    >
                                                        <Shield size={14} />
                                                    </button>
                                                    <button
                                                        onClick={() => toggleActive(u)}
                                                        className="btn-ghost btn-sm"
                                                        title={u.is_active ? 'Deactivate' : 'Activate'}
                                                    >
                                                        {u.is_active ? <ToggleRight size={14} /> : <ToggleLeft size={14} />}
                                                    </button>
                                                    <button
                                                        onClick={() => setResetTarget(u)}
                                                        className="btn-ghost btn-sm"
                                                        title="Reset password"
                                                    >
                                                        <KeyRound size={14} />
                                                    </button>
                                                    <button
                                                        onClick={() => deleteUser(u)}
                                                        className="btn-ghost btn-sm text-accent-danger hover:text-accent-danger"
                                                        title="Deactivate user"
                                                    >
                                                        <Ban size={14} />
                                                    </button>
                                                </>
                                            )}
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {showCreate && (
                <CreateUserModal
                    onClose={() => setShowCreate(false)}
                    onCreated={fetchUsers}
                />
            )}

            {resetTarget && (
                <ResetPasswordModal
                    targetUser={resetTarget}
                    onClose={() => setResetTarget(null)}
                    onReset={fetchUsers}
                />
            )}
        </div>
    )
}

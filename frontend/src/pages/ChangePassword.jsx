import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Shield, KeyRound } from 'lucide-react'
import toast from 'react-hot-toast'
import { useAuthStore } from '../stores'

export default function ChangePassword() {
    const navigate = useNavigate()
    const { user, changePassword, loading } = useAuthStore()
    const [form, setForm] = useState({
        currentPassword: '',
        newPassword: '',
        confirmPassword: '',
    })

    const onSubmit = async (e) => {
        e.preventDefault()
        if (form.newPassword !== form.confirmPassword) {
            toast.error('Passwords do not match')
            return
        }
        if (form.newPassword.length < 8) {
            toast.error('New password must be at least 8 characters')
            return
        }
        try {
            await changePassword(form.currentPassword, form.newPassword)
            toast.success('Password changed successfully')
            navigate('/', { replace: true })
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Password change failed')
        }
    }

    return (
        <div className="min-h-screen bg-dark-950 flex items-center justify-center p-4">
            <div className="card w-full max-w-md">
                <div className="flex items-center gap-3 mb-6">
                    <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-accent-primary to-accent-secondary flex items-center justify-center shadow-lg">
                        <Shield className="text-white" size={20} />
                    </div>
                    <div>
                        <h1 className="text-xl font-bold text-dark-50">Change Password</h1>
                        <p className="text-dark-400 text-sm">
                            {user?.must_change_password
                                ? 'You must change your password before continuing'
                                : `Updating password for ${user?.username}`}
                        </p>
                    </div>
                </div>

                {user?.must_change_password && (
                    <div className="mb-4 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-sm">
                        <KeyRound size={14} className="inline mr-2" />
                        Your account requires a password change before you can proceed.
                    </div>
                )}

                <form onSubmit={onSubmit} className="space-y-3">
                    <div>
                        <label className="block text-sm text-dark-300 mb-1">Current Password</label>
                        <input
                            type="password"
                            className="input"
                            value={form.currentPassword}
                            onChange={(e) => setForm({ ...form, currentPassword: e.target.value })}
                            required
                            autoFocus
                        />
                    </div>

                    <div>
                        <label className="block text-sm text-dark-300 mb-1">New Password</label>
                        <input
                            type="password"
                            className="input"
                            value={form.newPassword}
                            onChange={(e) => setForm({ ...form, newPassword: e.target.value })}
                            required
                            minLength={8}
                        />
                    </div>

                    <div>
                        <label className="block text-sm text-dark-300 mb-1">Confirm New Password</label>
                        <input
                            type="password"
                            className="input"
                            value={form.confirmPassword}
                            onChange={(e) => setForm({ ...form, confirmPassword: e.target.value })}
                            required
                            minLength={8}
                        />
                    </div>

                    <button
                        type="submit"
                        disabled={loading}
                        className="btn-primary w-full disabled:opacity-60 flex items-center justify-center gap-2"
                    >
                        <KeyRound size={16} />
                        {loading ? 'Please wait...' : 'Change Password'}
                    </button>
                </form>
            </div>
        </div>
    )
}

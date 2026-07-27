import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Shield, LogIn } from 'lucide-react'
import toast from 'react-hot-toast'
import { useAuthStore } from '../stores'

export default function Login() {
    const navigate = useNavigate()
    const { login, loading } = useAuthStore()
    const [form, setForm] = useState({
        username: '',
        password: '',
    })

    const onSubmit = async (e) => {
        e.preventDefault()
        try {
            await login(form.username, form.password)
            toast.success('Welcome back')
            navigate('/', { replace: true })
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Authentication failed')
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
                        <h1 className="text-xl font-bold text-dark-50">Bars</h1>
                        <p className="text-dark-400 text-sm">Operator Authentication</p>
                    </div>
                </div>

                <form onSubmit={onSubmit} className="space-y-3">
                    <div>
                        <label className="block text-sm text-dark-300 mb-1">Username</label>
                        <input
                            className="input"
                            value={form.username}
                            onChange={(e) => setForm({ ...form, username: e.target.value })}
                            required
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
                    </div>

                    <button
                        type="submit"
                        disabled={loading}
                        className="btn-primary w-full disabled:opacity-60 flex items-center justify-center gap-2"
                    >
                        <LogIn size={16} />
                        {loading ? 'Please wait...' : 'Sign In'}
                    </button>
                </form>
            </div>
        </div>
    )
}

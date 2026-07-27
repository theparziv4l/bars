import { useState, useEffect } from 'react'
import { GitMerge, Plus, Trash2, Copy, Loader2, RefreshCw } from 'lucide-react'
import { syncPeersApi } from '../services/api'
import toast from 'react-hot-toast'

export default function SyncPeers() {
    const [nodeInfo, setNodeInfo] = useState(null)
    const [peers, setPeers] = useState([])
    const [loading, setLoading] = useState(true)
    const [syncDisabled, setSyncDisabled] = useState(false)
    const [adding, setAdding] = useState(false)
    const [syncing, setSyncing] = useState(false)
    const [form, setForm] = useState({ base_url: '', label: '' })

    const fetchData = async () => {
        setLoading(true)
        setSyncDisabled(false)
        try {
            const [nodeRes, peersRes] = await Promise.all([
                syncPeersApi.nodeInfo(),
                syncPeersApi.list(),
            ])
            setNodeInfo(nodeRes.data)
            setPeers(peersRes.data)
        } catch (err) {
            if (err?.response?.status === 503) {
                setSyncDisabled(true)
                setPeers([])
                try {
                    const nodeRes = await syncPeersApi.nodeInfo()
                    setNodeInfo(nodeRes.data)
                } catch {
                    setNodeInfo({ node_id: '—', peer_url: null })
                }
            } else {
                toast.error('Failed to load sync settings')
                console.error(err)
            }
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        fetchData()
    }, [])

    const copyUrl = (url) => {
        navigator.clipboard.writeText(url).then(() => toast.success('Copied to clipboard'))
    }

    const handleAdd = async (e) => {
        e.preventDefault()
        const base_url = (form.base_url || '').trim()
        if (!base_url) {
            toast.error('Enter a URL (e.g. http://192.168.1.20:8000)')
            return
        }
        setAdding(true)
        try {
            await syncPeersApi.add({
                base_url: base_url.includes('://') ? base_url : `http://${base_url}`,
                label: (form.label || '').trim() || null,
            })
            toast.success('Peer added')
            setForm({ base_url: '', label: '' })
            fetchData()
        } catch (err) {
            const msg = err?.response?.data?.detail || 'Failed to add peer'
            toast.error(typeof msg === 'string' ? msg : JSON.stringify(msg))
        } finally {
            setAdding(false)
        }
    }

    const handleDelete = async (id) => {
        if (!confirm('Remove this sync peer?')) return
        try {
            await syncPeersApi.delete(id)
            toast.success('Peer removed')
            fetchData()
        } catch (err) {
            toast.error('Failed to remove peer')
        }
    }

    const handleSyncNow = async () => {
        setSyncing(true)
        try {
            await syncPeersApi.runSync()
            toast.success('Sync completed')
        } catch (err) {
            const msg = err?.response?.data?.detail || 'Sync failed'
            toast.error(typeof msg === 'string' ? msg : 'Sync failed')
        } finally {
            setSyncing(false)
        }
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center h-64">
                <Loader2 className="w-8 h-8 animate-spin text-accent-primary" />
            </div>
        )
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-accent-primary/20">
                    <GitMerge className="w-6 h-6 text-accent-primary" />
                </div>
                <div>
                    <h1 className="text-xl font-semibold text-dark-50">Sync & Collaboration</h1>
                    <p className="text-sm text-dark-400">
                        Connect to other operators’ instances to sync projects and data over the network.
                    </p>
                </div>
            </div>

            {syncDisabled && (
                <div className="glass-card p-4 border border-amber-500/30 bg-amber-500/5 rounded-xl">
                    <p className="text-amber-200 text-sm">
                        Sync is disabled. Enable <code className="px-1 rounded bg-dark-800">SYNC_ENABLED=true</code> and
                        set a unique <code className="px-1 rounded bg-dark-800">NODE_ID</code> in your backend environment to use sync peers.
                    </p>
                </div>
            )}

            {/* Sync now */}
            <div className="glass-card p-6 rounded-xl border border-dark-700">
                <h2 className="text-lg font-medium text-dark-200 mb-3">Sync database</h2>
                <p className="text-sm text-dark-400 mb-4">
                    Sync with all configured peers now (pull remote changes and push local changes). Sync also runs automatically on an interval.
                </p>
                <button
                    type="button"
                    onClick={handleSyncNow}
                    disabled={syncDisabled || syncing}
                    className="btn-primary inline-flex items-center gap-2 px-4 py-2 rounded-lg"
                >
                    {syncing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw size={18} />}
                    {syncing ? 'Syncing…' : 'Sync now'}
                </button>
            </div>

            {/* Your node */}
            <div className="glass-card p-6 rounded-xl border border-dark-700">
                <h2 className="text-lg font-medium text-dark-200 mb-3">Your node</h2>
                <p className="text-sm text-dark-400 mb-2">
                    Share this with collaborators so they can add you as a peer on their instance.
                </p>
                <div className="flex flex-wrap items-center gap-3">
                    <span className="text-xs text-dark-500">Node ID:</span>
                    <span className="font-mono text-dark-200">{nodeInfo?.node_id ?? '—'}</span>
                    {nodeInfo?.peer_url ? (
                        <div className="flex items-center gap-2">
                            <code className="px-2 py-1 rounded bg-dark-800 text-accent-primary font-mono text-sm">
                                {nodeInfo.peer_url}
                            </code>
                            <button
                                type="button"
                                onClick={() => copyUrl(nodeInfo.peer_url)}
                                className="p-1.5 rounded-lg hover:bg-dark-700 text-dark-300 hover:text-dark-100"
                                title="Copy"
                            >
                                <Copy size={16} />
                            </button>
                        </div>
                    ) : (
                        <p className="text-sm text-dark-400">
                            Use your computer’s IP and port 8000 (e.g. <code className="px-1 rounded bg-dark-800">http://YOUR_IP:8000</code>).
                            Optionally set <code className="px-1 rounded bg-dark-800">PUBLIC_BASE_URL</code> in the backend to show a copyable URL here.
                        </p>
                    )}
                </div>
            </div>

            {/* Sync peers */}
            <div className="glass-card p-6 rounded-xl border border-dark-700">
                <h2 className="text-lg font-medium text-dark-200 mb-4">Sync peers</h2>
                <p className="text-sm text-dark-400 mb-4">
                    Add another operator’s backend URL to sync databases. They must add your node on their side for two-way sync.
                </p>

                <form onSubmit={handleAdd} className="flex flex-wrap gap-3 mb-6">
                    <input
                        type="text"
                        placeholder="http://192.168.1.20:8000"
                        value={form.base_url}
                        onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
                        className="input-glass flex-1 min-w-[200px] px-4 py-2 rounded-lg bg-dark-800 border border-dark-600 text-dark-100 placeholder-dark-500"
                        disabled={syncDisabled}
                    />
                    <input
                        type="text"
                        placeholder="Label (e.g. Operator2)"
                        value={form.label}
                        onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
                        className="input-glass w-40 px-4 py-2 rounded-lg bg-dark-800 border border-dark-600 text-dark-100 placeholder-dark-500"
                        disabled={syncDisabled}
                    />
                    <button
                        type="submit"
                        disabled={syncDisabled || adding}
                        className="btn-primary flex items-center gap-2 px-4 py-2 rounded-lg"
                    >
                        {adding ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus size={18} />}
                        Add peer
                    </button>
                </form>

                {peers.length === 0 ? (
                    <p className="text-dark-500 text-sm">No peers added yet. Add a collaborator’s backend URL above.</p>
                ) : (
                    <ul className="space-y-2">
                        {peers.map((p) => (
                            <li
                                key={p.id}
                                className="flex items-center justify-between py-2 px-3 rounded-lg bg-dark-800/50 border border-dark-700"
                            >
                                <div className="flex items-center gap-3">
                                    <span className="font-mono text-sm text-dark-200">{p.base_url}</span>
                                    {p.label && (
                                        <span className="text-xs px-2 py-0.5 rounded bg-dark-700 text-dark-300">
                                            {p.label}
                                        </span>
                                    )}
                                </div>
                                <button
                                    type="button"
                                    onClick={() => handleDelete(p.id)}
                                    disabled={syncDisabled}
                                    className="p-1.5 rounded-lg hover:bg-dark-700 text-dark-400 hover:text-accent-danger"
                                    title="Remove peer"
                                >
                                    <Trash2 size={16} />
                                </button>
                            </li>
                        ))}
                    </ul>
                )}
            </div>
        </div>
    )
}

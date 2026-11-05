import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'

// rows: [{ id, label, sub?, children?: [{id,label}] }]; onDelete(id, isChild) deletes one.
// Checked group = whole group deleted (children go with it); children can be ticked alone.
export default function BulkDeleteModal({ title, rows, onDelete, onClose, onDone }) {
    const [sel, setSel] = useState(new Set()) // keys: "r:<id>" or "c:<id>"
    const [busy, setBusy] = useState(false)

    const toggle = (k) => setSel(s => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n })
    const allKeys = rows.map(r => `r:${r.id}`)
    const allOn = rows.length > 0 && allKeys.every(k => sel.has(k))

    const run = async () => {
        if (!confirm(`Delete ${sel.size} selected?`)) return
        setBusy(true)
        let fail = 0
        for (const r of rows) {
            if (sel.has(`r:${r.id}`)) {
                try { await onDelete(r.id, false) } catch { fail++ }
                continue // children die with the parent
            }
            for (const c of r.children || []) {
                if (!sel.has(`c:${c.id}`)) continue
                try { await onDelete(c.id, true) } catch { fail++ }
            }
        }
        setBusy(false)
        fail ? toast.error(`${fail} failed`) : toast.success('Deleted')
        onDone()
        onClose()
    }

    return (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
            <div className="card max-w-lg w-full flex flex-col max-h-[80vh]">
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-xl font-bold text-dark-50">{title}</h2>
                    <button onClick={onClose} className="btn-ghost btn-sm">✕</button>
                </div>
                <label className="flex items-center gap-2 mb-2 text-sm text-dark-300 cursor-pointer">
                    <input type="checkbox" checked={allOn}
                        onChange={() => setSel(allOn ? new Set() : new Set(allKeys))} />
                    Select all
                </label>
                <div className="flex-1 overflow-y-auto space-y-1 mb-4 pr-2">
                    {rows.length === 0 && <div className="text-center py-8 text-dark-400">Nothing to delete</div>}
                    {rows.map(r => (
                        <div key={r.id}>
                            <label className="flex items-center gap-2 p-2 rounded bg-dark-800 cursor-pointer">
                                <input type="checkbox" checked={sel.has(`r:${r.id}`)} onChange={() => toggle(`r:${r.id}`)} />
                                <span className="text-dark-100">{r.label}</span>
                                {r.sub && <span className="text-xs text-dark-500">{r.sub}</span>}
                            </label>
                            {(r.children || []).map(c => (
                                <label key={c.id} className="flex items-center gap-2 py-1 pl-8 text-sm cursor-pointer">
                                    <input type="checkbox" disabled={sel.has(`r:${r.id}`)}
                                        checked={sel.has(`r:${r.id}`) || sel.has(`c:${c.id}`)}
                                        onChange={() => toggle(`c:${c.id}`)} />
                                    <span className="text-dark-300">{c.label}</span>
                                </label>
                            ))}
                        </div>
                    ))}
                </div>
                <div className="flex gap-3 pt-4 border-t border-dark-700">
                    <button onClick={onClose} className="btn-secondary flex-1">Cancel</button>
                    <button onClick={run} disabled={!sel.size || busy}
                        className="btn-primary flex-1 disabled:opacity-50">
                        {busy ? <Loader2 className="animate-spin mx-auto" size={16} /> : `Delete Selected (${sel.size})`}
                    </button>
                </div>
            </div>
        </div>
    )
}

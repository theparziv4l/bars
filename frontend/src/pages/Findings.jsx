import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import { useFindingsStore } from '../stores'
import { evidenceApi } from '../services/api'

function FindingExtras({ finding, onPatch }) {
    const [notes, setNotes] = useState(finding.notes || '')
    const [evidence, setEvidence] = useState([])
    const [busy, setBusy] = useState(false)

    useEffect(() => {
        let alive = true
        evidenceApi.list(finding.id).then(({ data }) => { if (alive) setEvidence(data) }).catch(() => {})
        return () => { alive = false }
    }, [finding.id])

    const saveNotes = async () => {
        try { await onPatch(finding, { notes }); toast.success('Notes saved') } catch { toast.error('Save failed') }
    }
    const upload = async (e) => {
        const file = e.target.files?.[0]
        if (!file) return
        const fd = new FormData()
        fd.append('file', file)
        fd.append('finding_id', finding.id)
        if (finding.project_id) fd.append('project_id', finding.project_id)
        setBusy(true)
        try {
            const { data } = await evidenceApi.uploadFile(fd)
            setEvidence((ev) => [data, ...ev]); toast.success('Evidence added')
        } catch { toast.error('Upload failed') } finally { setBusy(false); e.target.value = '' }
    }
    const attachText = async () => {
        const content = window.prompt('Paste evidence text / command output:')
        if (!content) return
        try {
            const { data } = await evidenceApi.attachText({ content, finding_id: finding.id, project_id: finding.project_id, caption: 'Output excerpt' })
            setEvidence((ev) => [data, ...ev]); toast.success('Text attached')
        } catch { toast.error('Attach failed') }
    }
    const removeEv = async (id) => {
        try { await evidenceApi.delete(id); setEvidence((ev) => ev.filter((x) => x.id !== id)) } catch { toast.error('Delete failed') }
    }

    return (
        <div className="mt-3 space-y-3 border-t border-dark-700 pt-3">
            <div>
                <label className="text-xs text-dark-500">Notes</label>
                <textarea value={notes} onChange={(e) => setNotes(e.target.value)}
                          className="w-full h-16 bg-dark-900 border border-dark-600 rounded px-3 py-2 text-dark-100 text-sm"
                          placeholder="Analyst notes…" />
                <button onClick={saveNotes} className="mt-1 text-xs px-3 py-1 rounded bg-dark-700 text-dark-200 hover:bg-dark-600">Save notes</button>
            </div>
            <div>
                <div className="flex items-center gap-3 mb-1">
                    <label className="text-xs text-dark-500">Evidence ({evidence.length})</label>
                    <label className="text-xs text-accent-primary cursor-pointer">
                        + File<input type="file" className="hidden" onChange={upload} disabled={busy} />
                    </label>
                    <button onClick={attachText} className="text-xs text-accent-primary">+ Text/Output</button>
                </div>
                <div className="space-y-1">
                    {evidence.map((ev) => (
                        <div key={ev.id} className="flex items-center gap-2 text-xs text-dark-300">
                            {ev.local_path
                                ? <a href={evidenceApi.serve(ev.id)} target="_blank" rel="noreferrer" className="text-accent-primary">{ev.filename || ev.kind}</a>
                                : <span className="font-mono truncate max-w-md">{(ev.content || '').slice(0, 80)}</span>}
                            {ev.caption && <span className="text-dark-500">— {ev.caption}</span>}
                            <button onClick={() => removeEv(ev.id)} className="text-red-400 ml-auto">remove</button>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    )
}

const SEV = {
    critical: { label: 'Critical', cls: 'bg-red-500/20 text-red-300 border-red-500/40' },
    high: { label: 'High', cls: 'bg-orange-500/20 text-orange-300 border-orange-500/40' },
    medium: { label: 'Medium', cls: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/40' },
    low: { label: 'Low', cls: 'bg-blue-500/20 text-blue-300 border-blue-500/40' },
    info: { label: 'Info', cls: 'bg-gray-500/20 text-gray-300 border-gray-500/40' },
}
const SEV_ORDER = ['critical', 'high', 'medium', 'low', 'info']
const STATUSES = ['open', 'confirmed', 'remediated', 'accepted', 'false_positive']

const inputCls = 'bg-dark-900 border border-dark-600 rounded px-3 py-2 text-dark-100 text-sm'
const selCls = 'bg-dark-900 border border-dark-600 rounded px-2 py-1 text-dark-200 text-xs'

function NewFindingModal({ projectId, onClose, onCreate }) {
    const [form, setForm] = useState({ title: '', severity: 'medium', description: '', remediation: '' })
    const [saving, setSaving] = useState(false)

    const submit = async (e) => {
        e.preventDefault()
        if (!form.title.trim()) return toast.error('Title required')
        setSaving(true)
        try {
            await onCreate({ project_id: Number(projectId), ...form })
            toast.success('Finding created')
            onClose()
        } catch {
            toast.error('Create failed')
        } finally {
            setSaving(false)
        }
    }

    return (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
            <form onClick={(e) => e.stopPropagation()} onSubmit={submit}
                  className="glass-card !bg-dark-800 p-6 w-full max-w-lg space-y-3 mx-4">
                <h2 className="text-lg font-semibold text-dark-100">New Finding</h2>
                <input autoFocus placeholder="Title" value={form.title}
                       onChange={(e) => setForm({ ...form, title: e.target.value })} className={`w-full ${inputCls}`} />
                <select value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })}
                        className={`w-full ${inputCls}`}>
                    {SEV_ORDER.map((s) => <option key={s} value={s}>{SEV[s].label}</option>)}
                </select>
                <textarea placeholder="Description" value={form.description}
                          onChange={(e) => setForm({ ...form, description: e.target.value })}
                          className={`w-full h-20 ${inputCls}`} />
                <textarea placeholder="Remediation" value={form.remediation}
                          onChange={(e) => setForm({ ...form, remediation: e.target.value })}
                          className={`w-full h-20 ${inputCls}`} />
                <div className="flex justify-end gap-2">
                    <button type="button" onClick={onClose} className="px-4 py-2 rounded text-dark-300 hover:bg-dark-700">Cancel</button>
                    <button type="submit" disabled={saving}
                            className="px-4 py-2 rounded bg-accent-primary text-white disabled:opacity-50">
                        {saving ? 'Saving…' : 'Create'}
                    </button>
                </div>
            </form>
        </div>
    )
}

export default function Findings() {
    const { projectId } = useParams()
    const { findings, summary, loading, fetchFindings, updateFinding, deleteFinding, createFinding } = useFindingsStore()
    const [sevFilter, setSevFilter] = useState('')
    const [statusFilter, setStatusFilter] = useState('')
    const [showNew, setShowNew] = useState(false)
    const [expanded, setExpanded] = useState(null)

    useEffect(() => {
        if (projectId) fetchFindings(projectId).catch(() => toast.error('Failed to load findings'))
    }, [projectId, fetchFindings])

    const filtered = findings.filter((f) =>
        (!sevFilter || f.severity === sevFilter) && (!statusFilter || f.status === statusFilter))

    const patch = async (f, body) => {
        try { await updateFinding(f.id, body) } catch { toast.error('Update failed') }
    }
    const remove = async (f) => {
        if (!window.confirm(`Delete finding "${f.title}"?`)) return
        try { await deleteFinding(f.id); toast.success('Deleted') } catch { toast.error('Delete failed') }
    }

    return (
        <div className="p-6 space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-semibold text-dark-100">Findings</h1>
                    <Link to={`/project/${projectId}`} className="text-sm text-accent-primary hover:underline">← Back to project</Link>
                </div>
                <button onClick={() => setShowNew(true)}
                        className="px-4 py-2 rounded bg-accent-primary text-white text-sm">+ New Finding</button>
            </div>

            <div className="flex flex-wrap gap-3">
                {SEV_ORDER.map((s) => (
                    <div key={s} className={`glass-card px-4 py-2 border ${SEV[s].cls}`}>
                        <div className="text-xs uppercase opacity-80">{SEV[s].label}</div>
                        <div className="text-2xl font-bold">{summary?.[s] ?? 0}</div>
                    </div>
                ))}
                <div className="glass-card px-4 py-2 border border-dark-600">
                    <div className="text-xs uppercase text-dark-400">Total</div>
                    <div className="text-2xl font-bold text-dark-100">{summary?.total ?? 0}</div>
                </div>
            </div>

            <div className="flex gap-3">
                <select value={sevFilter} onChange={(e) => setSevFilter(e.target.value)} className={inputCls}>
                    <option value="">All severities</option>
                    {SEV_ORDER.map((s) => <option key={s} value={s}>{SEV[s].label}</option>)}
                </select>
                <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className={inputCls}>
                    <option value="">All statuses</option>
                    {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
            </div>

            {loading ? (
                <div className="text-dark-400">Loading…</div>
            ) : filtered.length === 0 ? (
                <div className="text-dark-400">
                    No findings yet. They appear automatically when a check&apos;s alert pattern matches, or add one manually.
                </div>
            ) : (
                <div className="space-y-2">
                    {filtered.map((f) => (
                        <div key={f.id} className="glass-card p-4">
                            <div className="flex items-center gap-3 flex-wrap">
                                <span className={`px-2 py-0.5 rounded text-xs border ${SEV[f.severity]?.cls}`}>
                                    {SEV[f.severity]?.label || f.severity}
                                </span>
                                <button className="flex-1 min-w-[12rem] text-left font-medium text-dark-100 hover:text-accent-primary"
                                        onClick={() => setExpanded(expanded === f.id ? null : f.id)}>
                                    {f.title}
                                    {f.occurrences > 1 && <span className="text-dark-400 text-xs ml-2">×{f.occurrences}</span>}
                                </button>
                                {f.host_id && <Link to={`/host/${f.host_id}`} className="text-xs text-accent-primary">host #{f.host_id}</Link>}
                                <select value={f.severity} onChange={(e) => patch(f, { severity: e.target.value })} className={selCls}>
                                    {SEV_ORDER.map((s) => <option key={s} value={s}>{SEV[s].label}</option>)}
                                </select>
                                <select value={f.status} onChange={(e) => patch(f, { status: e.target.value })} className={selCls}>
                                    {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                                </select>
                                <button onClick={() => remove(f)} className="text-red-400 hover:text-red-300 text-sm">Delete</button>
                            </div>
                            {expanded === f.id && (
                                <div className="mt-3 space-y-2 text-sm text-dark-300 border-t border-dark-700 pt-3">
                                    {f.description && <p><span className="text-dark-500">Description: </span>{f.description}</p>}
                                    {f.evidence_text && <p className="font-mono text-xs bg-dark-900 p-2 rounded overflow-x-auto">{f.evidence_text}</p>}
                                    {f.remediation && <p><span className="text-dark-500">Remediation: </span>{f.remediation}</p>}
                                    {(f.cwe || f.cve || f.cvss_score) && (
                                        <p className="text-xs text-dark-400">
                                            {[f.cwe, f.cve, f.cvss_score && `CVSS ${f.cvss_score}`].filter(Boolean).join(' · ')}
                                        </p>
                                    )}
                                    <p className="text-xs text-dark-500">
                                        source: {f.source}{f.discovered_at ? ` · discovered ${new Date(f.discovered_at).toLocaleString()}` : ''}
                                    </p>
                                    <FindingExtras finding={f} onPatch={patch} />
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}

            {showNew && <NewFindingModal projectId={projectId} onClose={() => setShowNew(false)} onCreate={createFinding} />}
        </div>
    )
}
